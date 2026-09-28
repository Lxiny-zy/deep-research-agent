"""工作台 HTTP 接口：任务模板目录、交付物登记、交付物下载、任务契约预览。

独立成 router 而不是继续往 ``api.py`` 里加路由：工作台是一块自洽的能力，
它的路由、响应模型和错误语义都应该能单独阅读。鉴权沿用全局的
``require_api_key``（含按 ``run_id`` 的属主校验），挂载时统一注入。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..blocking import run_blocking
from .contract import build_contract
from .publish import DeliveryBundle, build_bundle, resolve_template
from .templates import get_template, public_templates

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
    payload["rendered"] = contract.render()
    return payload


async def _bundle(request: Request, run_id: str) -> DeliveryBundle:
    repo = request.app.state.repo
    detail = await repo.get_run(run_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    if detail.status not in _TERMINAL:
        raise HTTPException(
            409, {"code": "run_not_finished", "message": "研究尚未结束，交付物还未生成"}
        )
    if detail.report is None:
        raise HTTPException(404, {"code": "no_report", "message": "本次运行没有生成正文"})
    # 交付包按需生成且结果确定；同一进程内按 (run, 报告哈希) 缓存，避免每次下载重排 PDF。
    cache: dict[tuple[str, int], DeliveryBundle] = (
        getattr(request.app.state, "delivery_cache", None) or {}
    )
    request.app.state.delivery_cache = cache
    key = (run_id, hash((detail.report.markdown, tuple(detail.report.citations))))
    bundle = cache.get(key)
    if bundle is not None:
        return bundle
    # 交付面板会同时请求登记表和预览文件；冷缓存时让并发请求共用同一次生成，
    # 而不是各自把 PDF / DOCX / PPTX 全部重排一遍。
    pending: dict[tuple[str, int], asyncio.Future[DeliveryBundle]] = (
        getattr(request.app.state, "delivery_pending", None) or {}
    )
    request.app.state.delivery_pending = pending
    inflight = pending.get(key)
    if inflight is not None:
        try:
            return await asyncio.shield(inflight)
        except asyncio.CancelledError:
            if not inflight.cancelled():
                raise  # 是本请求自己被取消
            # 负责生成的那个请求断开了：由本请求接手重新生成
    future: asyncio.Future[DeliveryBundle] = asyncio.get_running_loop().create_future()
    pending[key] = future
    try:
        bundle = await run_blocking(build_bundle, detail)
    except asyncio.CancelledError:
        future.cancel()
        raise
    except Exception as exc:
        future.set_exception(exc)
        future.exception()  # 已由本请求抛出；标记为已取回，避免无人等待时的告警
        raise
    finally:
        if pending.get(key) is future:
            pending.pop(key)
    future.set_result(bundle)
    if len(cache) > 32:
        cache.pop(next(iter(cache)))
    cache[key] = bundle
    return bundle


@router.get("/runs/{run_id}/deliverables")
async def get_deliverables(run_id: str, request: Request) -> dict[str, Any]:
    """交付登记：每个交付物的格式、角色、大小、哈希与验收结论，外加每道门的结果。"""
    bundle = await _bundle(request, run_id)
    registry = bundle.registry()
    registry["run_id"] = run_id
    return registry


@router.get("/runs/{run_id}/deliverables/{name:path}")
async def download_deliverable(run_id: str, name: str, request: Request) -> Response:
    if not all(_NAME_RE.fullmatch(part) for part in name.split("/")) or name.count("/") > 1:
        raise HTTPException(400, "invalid deliverable name")
    bundle = await _bundle(request, run_id)
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
async def upload_attachment(req: AttachmentUpload) -> dict[str, Any]:
    """解析一个上传文件：返回附件（含全部片段，供创建任务时原样提交）与展示摘要。

    解析只在内存中进行，原始文件不落盘；创建任务时片段随任务契约冻结进 checkpoint。
    """
    import base64
    import binascii

    from .attachments import AttachmentError, parse_attachment

    try:
        raw = base64.b64decode(req.data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, "文件内容不是有效的 Base64") from exc
    try:
        attachment = await parse_attachment(raw, req.filename, req.mime_type)
    except AttachmentError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"attachment": attachment.model_dump(mode="json"), "summary": attachment.summary()}
