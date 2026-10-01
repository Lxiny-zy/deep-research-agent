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

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..artifacts import ArtifactError
from ..blocking import run_blocking
from .contract import build_contract, pasted_paper_text
from .delivery_store import build_or_load, load_version
from .publish import DeliveryBundle, build_bundle, delivery_fingerprint, resolve_template
from .templates import get_template, public_templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["workbench"])

_TERMINAL = {"done", "error", "cancelled"}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


class ContractPreviewRequest(BaseModel):
    template: str = Field(min_length=1, max_length=40)
    query: str = Field(min_length=1, max_length=200_000)
    strategy: Literal["none", "quick", "deep"] | None = None


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
    try:
        return await _stored_bundle(request, run_id, version)
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
    key = (run_id, delivery_fingerprint(detail))
    bundle = cache.get(key)
    if bundle is not None:
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
            generated = await run_blocking(
                build_or_load,
                detail,
                settings.artifact_root,
                settings.artifact_total_bytes,
                build_bundle,
            )
            if len(cache) >= 32:
                cache.pop(next(iter(cache)))
            cache[key] = generated
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
    from .reader import paper_sources, reader_documents

    detail = await request.app.state.repo.get_run(run_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    return {
        "run_id": run_id,
        "status": detail.status,
        "documents": reader_documents(detail),
        "has_report": detail.report is not None,
        "can_ask": detail.status == "done" and bool(paper_sources(detail)),
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
    }


__all__ = ["router"]


class AttachmentUpload(BaseModel):
    """上传一个任务附件（Base64 编码的文件内容）。"""

    filename: str = Field(min_length=1, max_length=300)
    mime_type: str = Field(default="", max_length=120)
    data_base64: str = Field(min_length=1, max_length=22_500_000)


@router.post("/attachments", status_code=201)
async def upload_attachment(req: AttachmentUpload, request: Request) -> dict[str, Any]:
    """解析一个上传文件：返回附件（含全部片段，供创建任务时原样提交）与展示摘要。

    解析在内存中进行，创建任务时片段随任务契约冻结进 checkpoint。PDF 另按内容哈希
    保存原文件，供论文精读工作区显示原版版面；其他格式的原文件不落盘。
    """
    import base64
    import binascii

    from ..artifacts import ArtifactError
    from .attachments import AttachmentError, parse_attachment, save_original

    try:
        raw = base64.b64decode(req.data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, "文件内容不是有效的 Base64") from exc
    try:
        attachment = await parse_attachment(raw, req.filename, req.mime_type)
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
