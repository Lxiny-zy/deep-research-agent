"""工作台 HTTP 接口：任务模板目录、交付物登记、交付物下载、任务契约预览。

独立成 router 而不是继续往 ``api.py`` 里加路由：工作台是一块自洽的能力，
它的路由、响应模型和错误语义都应该能单独阅读。鉴权沿用全局的
``require_api_key``（含按 ``run_id`` 的属主校验），挂载时统一注入。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..artifacts import ArtifactError
from ..blocking import run_blocking
from ..upload_limits import DOCUMENT_LIMIT_LABEL, DOCUMENT_MAX_BASE64_CHARS, DOCUMENT_MAX_BYTES
from .contract import build_contract, pasted_paper_text
from .delivery_store import DeliveryConflict, current_version, load_version
from .publish import DeliveryBundle, delivery_fingerprint, resolve_template
from .templates import get_template, public_templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["workbench"])

_TERMINAL = {"done", "needs_review", "error", "cancelled"}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


class ContractPreviewRequest(BaseModel):
    template: str = Field(min_length=1, max_length=40)
    query: str = Field(min_length=1, max_length=200_000)
    strategy: Literal["none", "quick", "deep"] | None = None


class ContentRevisionRequest(BaseModel):
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class DeliveryRetryRequest(BaseModel):
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    format: Literal["html", "pdf", "docx", "pptx", "png", "xlsx"]
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


@router.get("/templates")
async def list_templates() -> list[dict[str, Any]]:
    """全部科研任务模板（前端任务入口据此渲染）。"""
    return public_templates()


@router.get("/tiers")
async def list_tiers() -> list[dict[str, Any]]:
    """研究档位：轻量 / 标准 / 深度对应的运行上限。"""
    from .tiers import public_tiers

    return public_tiers()


@router.get("/usage")
async def get_usage(request: Request) -> dict[str, Any]:
    """当前身份今日（UTC）的研究次数与 token 用量，以及部署配置的额度。"""
    from ..http.auth import principal_for
    from .usage import quota_view, usage_today

    principal = principal_for(request)
    repo = request.app.state.repo
    return quota_view(await usage_today(repo, principal.id), request.app.state.settings)


@router.get("/config/quality-schema")
async def get_quality_schema() -> list[dict[str, Any]]:
    """交付质量设置的字段说明：标签、分组、取值范围、默认值与悬浮帮助文本。"""
    from .quality import quality_schema

    return quality_schema()


@router.post("/templates/contract")
async def preview_contract(req: ContractPreviewRequest, request: Request) -> dict[str, Any]:
    """预览系统将如何理解这次任务：用户提交前就能看到解析出的论文、章节与约束。"""
    template = get_template(req.template)
    if template is None:
        raise HTTPException(404, "unknown template")
    if not template.supports(req.strategy):
        raise HTTPException(422, "unsupported strategy")
    contract = build_contract(
        template, req.query, strategy=req.strategy, quality=request.app.state.settings.quality
    )
    payload = contract.model_dump(mode="json")
    payload["dataset_csv"] = contract.dataset_csv[:2000]
    payload["dataset_rows"] = (
        max(0, contract.dataset_csv.count("\n")) if contract.dataset_csv else 0
    )
    if template.input_kind == "dataset" and contract.dataset_csv:
        # 用与分析时相同的解析规则预检：行列数、列类型，或者说明为什么不能分析
        from .analysis import DatasetError
        from .datasets import profile_csv

        try:
            profile = profile_csv("", contract.dataset_csv).profile()
            payload["dataset_profile"] = profile
            payload["dataset_rows"] = profile["rows"]
        except DatasetError as exc:
            payload["dataset_error"] = str(exc)
    payload["pasted_paper_chars"] = (
        len(pasted_paper_text(contract)) if template.input_kind == "paper" else 0
    )
    payload["rendered"] = contract.render()
    return payload


async def _bundle(request: Request, run_id: str, version: str | None = None) -> DeliveryBundle:
    from ..render_queue import RenderFailed

    try:
        return await _stored_bundle(request, run_id, version)
    except RenderFailed as exc:
        raise HTTPException(
            502, {"code": "render_failed", "message": str(exc), "job_id": exc.job_id}
        ) from exc
    except (ArtifactError, ValueError) as exc:
        raise HTTPException(
            409,
            {"code": "delivery_integrity", "message": "交付文件或版本登记校验失败，未覆盖已有文件"},
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(503, "交付物仍在生成，请稍后重试") from exc
    except OSError as exc:
        raise HTTPException(503, "交付文件读写失败，请检查服务存储后重试") from exc


async def _stored_bundle(
    request: Request, run_id: str, version: str | None = None
) -> DeliveryBundle:
    repo = request.app.state.repo
    detail = await repo.get_run(run_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    settings = request.app.state.settings
    if version is not None:
        try:
            return await run_blocking(load_version, detail, settings.artifact_root, version)
        except FileNotFoundError as exc:
            raise HTTPException(404, "交付版本不存在") from exc
    if detail.status not in _TERMINAL:
        raise HTTPException(
            409, {"code": "run_not_finished", "message": "研究尚未结束，交付物还未生成"}
        )
    if detail.report is None:
        raise HTTPException(404, {"code": "no_report", "message": "本次运行没有生成正文"})
    cache: dict[tuple[str, str], DeliveryBundle] | None = getattr(
        request.app.state, "delivery_cache", None
    )
    if cache is None:
        cache = {}
        request.app.state.delivery_cache = cache
    # Read the atomically published pointer: another API process may have
    # completed a retry since this process populated its immutable cache.
    active_version = await run_blocking(current_version, detail, settings.artifact_root)
    key = (run_id, active_version or delivery_fingerprint(detail))
    bundle = cache.get(key)
    if bundle is not None:
        from .completion import synchronize_completion

        await synchronize_completion(repo, run_id, settings)
        return bundle
    # 交付面板会同时请求登记表和预览文件；冷缓存时让并发请求共用同一次生成，
    # 而不是各自把 PDF / DOCX / PPTX 全部重排一遍。
    pending: dict[tuple[str, str], asyncio.Future[DeliveryBundle]] | None = getattr(
        request.app.state, "delivery_pending", None
    )
    if pending is None:
        pending = {}
        request.app.state.delivery_pending = pending
    inflight = pending.get(key)
    if inflight is not None:
        return await asyncio.shield(inflight)

    async def generate() -> DeliveryBundle:
        try:
            from ..api import _render_retry_token
            from ..render_service import service_for

            generated = await service_for(repo, settings).build(
                detail, retry_token=_render_retry_token(request)
            )
            if len(cache) >= 32:
                cache.pop(next(iter(cache)))
            cache[key] = generated
            from .completion import synchronize_completion

            await synchronize_completion(repo, run_id, settings)
            return generated
        finally:
            pending.pop(key, None)

    # The build belongs to the shared cache, not the first HTTP waiter. A
    # disconnected tab must not abandon the still-running render thread and
    # cause another request to start a duplicate build.
    task = asyncio.create_task(generate())
    pending[key] = task
    task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    return await asyncio.shield(task)


@router.get("/runs/{run_id}/deliverables")
async def get_deliverables(run_id: str, request: Request) -> dict[str, Any]:
    """交付登记：每个交付物的格式、角色、大小、哈希与验收结论，外加每道门的结果。"""
    bundle = await _bundle(request, run_id)
    registry = bundle.registry()
    registry["run_id"] = run_id
    from ..http.auth import principal_for

    registry["can_retry"] = principal_for(request).can_research
    from .content_revision import revision_offer

    detail = await request.app.state.repo.get_run(run_id)
    if detail is not None:
        registry["content_revision"] = revision_offer(detail, bundle.gates)
    return registry


@router.post("/runs/{run_id}/revise", status_code=202)
async def revise_content(
    run_id: str, body: ContentRevisionRequest, request: Request, response: Response
) -> dict[str, str]:
    from .. import api as api_module
    from ..http.auth import principal_for
    from ..orchestrator import snapshot_catalog_for_execution
    from ..persistence.repository import IdempotencyConflictError
    from .content_revision import prepare_revision, revision_offer, source_version
    from .support import digest

    principal = principal_for(request)
    if not principal.can_research:
        raise HTTPException(403, "当前身份为只读，无法继续修订")
    repo = request.app.state.repo
    detail = await repo.get_run(run_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    key = digest(["content-revision", principal.id, body.request_id])
    request_hash = digest([principal.id, run_id, body.source_version])
    try:
        existing = await repo.find_run_once(key, request_hash)
    except IdempotencyConflictError as exc:
        raise HTTPException(409, "修订请求标识已用于其他任务或版本") from exc
    if existing:
        response.headers["Idempotency-Replayed"] = "true"
        return {"run_id": existing}
    if source_version(detail) != body.source_version:
        raise HTTPException(409, "任务材料或正文已变化，请刷新后再继续修订")
    bundle = await _bundle(request, run_id)
    offer = revision_offer(detail, bundle.gates)
    if not offer["available"]:
        raise HTTPException(409, offer["reason"])
    await api_module._check_rate_limit(request)
    settings = request.app.state.settings
    if settings.daily_run_quota is not None:
        from .usage import quota_view, usage_today

        if quota_view(await usage_today(repo, principal.id), settings)["exhausted"]:
            raise HTTPException(429, "今日研究额度已用完，将于 UTC 零点重置")
    try:
        execution, current = await prepare_revision(detail, settings)
        await snapshot_catalog_for_execution(
            execution, getattr(request.app.state, "catalog", None), current
        )
        catalog = getattr(request.app.state, "catalog", None)
        if catalog is not None and hasattr(catalog, "list_search_profiles"):
            from ..catalog.dto import CatalogRuntimeSnapshot
            from ..catalog.preflight import inspect_snapshot
            from ..orchestrator import RUN_CATALOG_CHECKPOINT_KEY, workflow_catalog_roles
            from ..workflow import Workflow

            snapshot = execution.checkpoint["scratch"].get(RUN_CATALOG_CHECKPOINT_KEY)
            if snapshot is not None:
                ready = await inspect_snapshot(
                    catalog,
                    current,
                    CatalogRuntimeSnapshot.model_validate(snapshot),
                    workflow_catalog_roles(Workflow.model_validate(execution.definition)),
                )
                if not ready.ok:
                    raise ValueError("配置检查未通过：" + "；".join(ready.errors))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    latest = await repo.get_run(run_id)
    if (
        latest is None
        or latest.status not in {"done", "needs_review"}
        or source_version(latest) != body.source_version
    ):
        raise HTTPException(409, "原任务已变化，请刷新后再继续修订")
    result = await api_module._submit_prepared_run(
        request,
        response,
        api_module.CreateRunRequest(
            query=detail.query, workflow=execution.workflow_name, project_id=detail.project_id
        ),
        execution,
        current,
        str(execution.input.get("query") or detail.query),
        execution.workflow_name,
        normalized_key=key,
        lease_owner=uuid4().hex,
        request_hash=request_hash,
    )
    return {"run_id": result.run_id}


@router.post("/runs/{run_id}/deliverables/retry")
async def retry_deliverable(
    run_id: str, body: DeliveryRetryRequest, request: Request
) -> dict[str, Any]:
    from ..http.auth import principal_for

    if not principal_for(request).can_research:
        raise HTTPException(403, "当前身份为只读，无法重新生成交付物")
    detail = await request.app.state.repo.get_run(run_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    if detail.status not in _TERMINAL or detail.report is None:
        raise HTTPException(409, "研究尚未定稿，不能重试交付格式")
    pending = getattr(request.app.state, "delivery_pending", None)
    if pending is None:
        pending = {}
        request.app.state.delivery_pending = pending
    key = (run_id, f"retry:{body.version}:{body.format}:{body.request_id}")

    async def generate() -> DeliveryBundle:
        try:
            settings = request.app.state.settings
            from ..render_service import service_for

            generated = await service_for(request.app.state.repo, settings).retry(
                detail, body.version, body.format, body.request_id
            )
            from .completion import synchronize_completion

            if not await synchronize_completion(request.app.state.repo, run_id, settings):
                raise DeliveryConflict(
                    "delivery_state_changed", "交付版本已变化，请刷新后查看最新结果"
                )
            return generated
        finally:
            pending.pop(key, None)

    task = pending.get(key)
    if task is None:
        task = asyncio.create_task(generate())
        pending[key] = task
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    try:
        bundle = await asyncio.shield(task)
    except DeliveryConflict as exc:
        raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "交付版本不存在") from exc
    except (ArtifactError, ValueError) as exc:
        raise HTTPException(
            409, {"code": "delivery_integrity", "message": "交付快照或文件校验失败"}
        ) from exc
    except (OSError, TimeoutError) as exc:
        raise HTTPException(503, "交付重试尚未完成，请检查存储或稍后重试") from exc
    registry = bundle.registry()
    registry["run_id"] = run_id
    registry["can_retry"] = True
    return registry


@router.get("/runs/{run_id}/deliverables/{name:path}")
async def download_deliverable(
    run_id: str,
    name: str,
    request: Request,
    version: str | None = Query(None, pattern=r"^[0-9a-f]{64}$"),
) -> Response:
    if not all(_NAME_RE.fullmatch(part) for part in name.split("/")) or name.count("/") > 1:
        raise HTTPException(400, "invalid deliverable name")
    bundle = await _bundle(request, run_id, version)
    file = next((item for item in bundle.files if item.name == name), None)
    if file is None:
        raise HTTPException(404, "deliverable not found")
    record = file.record()
    download = request.query_params.get("download") != "0"
    filename = name.rsplit("/", 1)[-1]
    disposition = f'{"attachment" if download else "inline"}; filename="{filename}"'
    # 磁盘文件名只保留 ASCII；filename* 带上中文标题，下载后能认出是哪份报告
    title = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", file.title).strip(" ._")[:60]
    extension = filename.rsplit(".", 1)[-1] if "." in filename else ""
    if title and extension:
        disposition += f"; filename*=UTF-8''{quote(f'{title}.{extension}', safe='')}"
    headers = {
        "Content-Disposition": disposition,
        "Cache-Control": "private, no-store",
        "X-Content-SHA256": record["sha256"],
        "X-Content-Version": bundle.content_version,
        # 自包含 HTML 在浏览器里内联预览时也不允许执行脚本或加载外部资源
        "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
        + ("; script-src 'unsafe-inline'" if name.endswith("-mindmap.html") else ""),
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=file.data, media_type=record["mime_type"], headers=headers)


@router.get("/runs/{run_id}/workspace")
async def get_workspace(run_id: str, request: Request) -> dict[str, Any]:
    """三栏详情页的结构数据：步骤、重规划记录与产物文件树。"""
    from .workspace import build_workspace

    detail = await request.app.state.repo.get_run(run_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    return await run_blocking(build_workspace, detail, request.app.state.settings.artifact_root)


@router.get("/runs/{run_id}/workspace/file")
async def get_workspace_file(run_id: str, path: str, request: Request) -> Response:
    """读取一个清单登记过的产物文件（按清单哈希复核后返回，不接受任意路径）。"""
    from ..artifacts import ArtifactError
    from .workspace import read_workspace_file

    if len(path) > 512 or ".." in path.split("/"):
        raise HTTPException(400, "invalid path")
    detail = await request.app.state.repo.get_run(run_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    try:
        data, mime, truncated = await run_blocking(
            read_workspace_file, detail, request.app.state.settings.artifact_root, path
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, "file not found") from exc
    except (ArtifactError, ValueError) as exc:
        raise HTTPException(409, f"产物校验失败：{exc}") from exc
    headers = {
        "X-Truncated": "1" if truncated else "0",
        "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'inline; filename="{path.rsplit("/", 1)[-1]}"',
    }
    # HTML 产物按纯文本返回：工作区是审阅视图，不在浏览器里执行模型产出的页面
    media = "text/plain; charset=utf-8" if mime in {"text/html", "application/xhtml+xml"} else mime
    return Response(content=data, media_type=media, headers=headers)


@router.get("/runs/{run_id}/reader")
async def get_reader(run_id: str, request: Request) -> dict[str, Any]:
    """论文精读工作区：这次任务的研究对象，以及每份能否显示原版 PDF。"""
    from .qa_scope import task_qa_scope
    from .reader import reader_documents

    detail = await request.app.state.repo.get_run(run_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    scope = task_qa_scope(detail)
    return {
        "run_id": run_id,
        "status": detail.status,
        "documents": reader_documents(detail),
        "has_report": detail.report is not None,
        "can_ask": detail.status in {"done", "needs_review"} and bool(scope.sources),
        "qa_scope": scope.kind,
        "source_count": len(scope.sources),
        "query": scope.query,
    }


@router.get("/runs/{run_id}/reader/{document_id}/pdf")
async def get_reader_pdf(run_id: str, document_id: str, request: Request) -> Response:
    """原版 PDF：只接受该任务登记过的文档；归属由鉴权依赖按 run_id 校验。"""
    from .reader import ReaderError, load_pdf

    detail = await request.app.state.repo.get_run(run_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    try:
        data = await load_pdf(detail, document_id, request.app.state.settings)
    except ReaderError as exc:
        raise HTTPException(404, {"code": "pdf_unavailable", "message": str(exc)}) from exc
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Vary": "Authorization",
            "Content-Disposition": f'inline; filename="{document_id}.pdf"',
            "Content-Security-Policy": "default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/runs/{run_id}/narrative")
async def get_narrative(run_id: str, request: Request) -> dict[str, Any]:
    """人话进度叙述：按阶段聚合的一句话进展，事件的纯函数（不调用模型）。"""
    from .narrative import build_narrative

    repo = request.app.state.repo
    status = await repo.get_run_status(run_id)
    if status is None:
        raise HTTPException(404, "run not found")
    events = await repo.get_events(run_id, after_seq=0, limit=20_000)
    return build_narrative(events, run_status=status)


@router.get("/runs/{run_id}/template")
async def get_run_template(run_id: str, request: Request) -> dict[str, Any]:
    """本次运行使用的任务模板与任务契约（详情页展示「系统理解了什么」）。"""
    from .contract import contract_from_scratch
    from .writers import WORKBENCH_SCRATCH_KEY

    repo = request.app.state.repo
    detail = await repo.get_run(run_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    template = resolve_template(detail)
    checkpoint = detail.orchestration.checkpoint if detail.orchestration else {}
    scratch = checkpoint.get("scratch", {}) if isinstance(checkpoint, dict) else {}
    contract = contract_from_scratch(scratch) if isinstance(scratch, dict) else None
    workbench = scratch.get(WORKBENCH_SCRATCH_KEY) if isinstance(scratch, dict) else None
    extras = workbench.get("extras", {}) if isinstance(workbench, dict) else {}
    safe_extras = {
        key: value for key, value in extras.items() if key in {"score", "stats", "figures"}
    }
    analysis = scratch.get("analysis") if isinstance(scratch, dict) else None
    intake = scratch.get("intake_sources") if isinstance(scratch, dict) else None
    return {
        "template": template.to_public(),
        "contract": (
            {**contract.model_dump(mode="json"), "dataset_csv": contract.dataset_csv[:2000]}
            if contract is not None
            else None
        ),
        "extras": safe_extras,
        "analysis": analysis if isinstance(analysis, dict) else None,
        "intake": intake if isinstance(intake, dict) else None,
        "revision_source": scratch.get("content_revision") if isinstance(scratch, dict) else None,
    }


__all__ = ["router"]


class AttachmentUpload(BaseModel):
    """上传一个任务附件（Base64 编码的文件内容）。"""

    filename: str = Field(min_length=1, max_length=300)
    mime_type: str = Field(default="", max_length=120)
    data_base64: str = Field(min_length=1, max_length=DOCUMENT_MAX_BASE64_CHARS)


@router.post("/attachments", status_code=201)
async def upload_attachment(req: AttachmentUpload, request: Request) -> dict[str, Any]:
    """解析一个上传文件：返回附件（含全部片段，供创建任务时原样提交）与展示摘要。

    解析在内存中进行，创建任务时片段随任务契约冻结进 checkpoint。PDF 另按内容哈希
    保存原文件，供论文精读工作区显示原版版面；其他格式的原文件不落盘。
    """
    import base64
    import binascii

    try:
        raw = base64.b64decode(req.data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, "文件内容不是有效的 Base64") from exc
    return await _store_attachment(raw, req.filename, req.mime_type, request)


@router.post(
    "/attachments/file",
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def upload_attachment_file(
    request: Request, filename: str = Query(min_length=1, max_length=300)
) -> dict[str, Any]:
    """Read a binary file incrementally, with a limit before parsing or storage."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > DOCUMENT_MAX_BYTES:
        raise HTTPException(413, f"文件超过 {DOCUMENT_LIMIT_LABEL} 限制")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > DOCUMENT_MAX_BYTES:
            raise HTTPException(413, f"文件超过 {DOCUMENT_LIMIT_LABEL} 限制")
        content.extend(chunk)
    raw = bytes(content)
    del content
    mime_type = request.headers.get("content-type", "").split(";", 1)[0].strip()[:120]
    return await _store_attachment(raw, filename, mime_type, request)


async def _store_attachment(
    raw: bytes, filename: str, mime_type: str, request: Request
) -> dict[str, Any]:
    from .attachments import AttachmentError, parse_attachment, save_original

    try:
        attachment = await parse_attachment(raw, filename, mime_type)
    except AttachmentError as exc:
        raise HTTPException(422, str(exc)) from exc
    if attachment.kind == "pdf":
        try:
            await run_blocking(save_original, request.app.state.settings, attachment.id, raw)
            attachment = attachment.model_copy(update={"stored": True})
        except (ArtifactError, OSError) as exc:
            # 存储失败（配额满、磁盘错误）不影响解析结果的使用，只是精读时看不到原版版面
            logger.warning("failed to store original attachment %s: %s", attachment.id, exc)
    return {"attachment": attachment.model_dump(mode="json"), "summary": attachment.summary()}


from .dataset_merge import DatasetMerge  # noqa: E402


@router.post("/datasets/merge")
async def merge_dataset_tables(req: DatasetMerge) -> dict[str, Any]:
    from .analysis import DatasetError
    from .dataset_merge import merge_tables

    try:
        return await run_blocking(merge_tables, req)
    except DatasetError as exc:
        raise HTTPException(422, {"code": "dataset_invalid", "message": str(exc)}) from exc


class DatasetUpload(BaseModel):
    """上传一个待分析的表格文件（CSV / TSV / XLSX，Base64 编码）。"""

    filename: str = Field(min_length=1, max_length=300)
    data_base64: str = Field(min_length=1, max_length=22_500_000)


@router.post("/datasets")
async def parse_dataset_upload(req: DatasetUpload) -> dict[str, Any]:
    """解析表格文件：返回每张可用工作表的 CSV、行数与列类型，以及被跳过的表和原因。

    与附件一样不落盘：前端选定工作表后把该表的 CSV 随任务提交，服务端创建任务时
    按同一规则重新校验。多工作表由用户选择，这里不替用户挑。
    """
    import base64
    import binascii

    from .analysis import DatasetError
    from .datasets import parse_table_file

    try:
        raw = base64.b64decode(req.data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, "文件内容不是有效的 Base64") from exc
    try:
        return await run_blocking(parse_table_file, raw, req.filename)
    except DatasetError as exc:
        raise HTTPException(422, {"code": "dataset_invalid", "message": str(exc)}) from exc
