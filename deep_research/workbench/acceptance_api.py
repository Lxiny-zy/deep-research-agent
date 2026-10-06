"""Version-bound manual acceptance and minimal reproduction packages."""

from __future__ import annotations

import hashlib
import json
import os
import re
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from ..artifacts import ArtifactError
from ..blocking import run_blocking
from ..http.auth import principal_for
from ..report.service import ReportService
from .acceptance_context import TASK_WORK, build_context, safe_text
from .acceptance_models import AcceptanceRequest
from .acceptance_store import list_records, read_record, repeated_record, save_record
from .delivery_store import DeliveryConflict
from .publish import delivery_fingerprint
from .reading_map import ReadingMapPage, ReadingUnitDetail, reading_page, reading_unit


def private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(
    prefix="/api/runs/{run_id}/acceptance",
    tags=["acceptance"],
    dependencies=[Depends(private_response)],
)


@lru_cache(maxsize=1)
def package_code_sha256() -> str:
    """A deploy can identify shipped code even when no Git metadata is installed."""
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if path.is_symlink():
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def application_identity() -> dict[str, Any]:
    try:
        package = package_version("deep-research-agent")
    except PackageNotFoundError:
        package = None
    revision = os.environ.get("DR_APP_REVISION")
    if revision and not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", revision):
        revision = None
    return {
        "package_version": package,
        "revision": revision,
        "package_code_sha256": package_code_sha256(),
        "contract_version": 1,
    }


async def _detail(request: Request, run_id: str) -> Any:
    detail = await request.app.state.repo.get_run(run_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    return detail


async def _context(request: Request, run_id: str, version: str, hsi: bool) -> tuple[Any, dict]:
    detail = await _detail(request, run_id)
    document = await ReportService(request.app.state.repo).document(run_id, include_hsi_tables=hsi)
    if document.content_version != version or document.source_version != delivery_fingerprint(
        detail
    ):
        raise HTTPException(
            409,
            {
                "code": "document_version_changed",
                "message": "文档已变化，不能用新稿创建旧版验收记录",
                "current_version": document.content_version,
            },
        )
    context = await run_blocking(build_context, detail, document)
    context["application"] = application_identity()
    return detail, context


async def _storage_call(function: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return await run_blocking(function, *args, **kwargs)
    except FileNotFoundError as exc:
        raise HTTPException(404, "人工验收记录或所选交付版本不存在") from exc
    except DeliveryConflict as exc:
        raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc
    except (ArtifactError, ValueError) as exc:
        raise HTTPException(
            409, {"code": "acceptance_integrity", "message": "验收记录校验失败"}
        ) from exc
    except OSError as exc:
        raise HTTPException(503, "验收记录存储暂不可用") from exc


@router.get("/template")
async def get_template(run_id: str, request: Request) -> dict[str, Any]:
    await _detail(request, run_id)
    return {
        "schema_version": 1,
        "run_id": run_id,
        "application": application_identity(),
        "request_schema": AcceptanceRequest.model_json_schema(),
        "example": {
            "request_id": "manual-review-001",
            "document_version": "<from /document>",
            "phase": "initial",
            "conclusion": "pending",
            "issues": [],
        },
        "task_to_next_work": TASK_WORK,
        "guidance": [
            "先读取 /document 的 content_version，再用该版本读取 acceptance/context。",
            "人工未评价保持 pending；恢复创建新记录并引用 parent_record_id，不覆盖首次记录。",
            "仅选择要提交的问题片段与材料标识，不提交全对话、全文或凭据。",
            "应用 revision 未配置时为 null，不能把包版本当作已确认的发布 commit。",
        ],
    }


@router.get("/context")
async def get_context(
    run_id: str,
    request: Request,
    version: str = Query(pattern=r"^[0-9a-f]{64}$"),
    include_hsi_tables: bool = False,
) -> dict[str, Any]:
    _, context = await _context(request, run_id, version, include_hsi_tables)
    return {key: value for key, value in context.items() if not key.startswith("_")}


@router.get("/locations/{location_id}")
async def get_location(
    run_id: str,
    location_id: str,
    request: Request,
    version: str = Query(pattern=r"^[0-9a-f]{64}$"),
    include_hsi_tables: bool = False,
    offset: int = Query(default=0, ge=0),
    length: int = Query(default=600, ge=1, le=1200),
) -> dict[str, Any]:
    _, context = await _context(request, run_id, version, include_hsi_tables)
    location = next((row for row in context["locations"] if row["id"] == location_id), None)
    if location is None:
        raise HTTPException(404, "该版本中不存在所选位置")
    text = context["_texts"][location_id]
    selected = text[offset : offset + length]
    return {
        "document_version": version,
        "location": location,
        "offset": offset,
        "excerpt": safe_text(selected),
        "excerpt_redacted": safe_text(selected) != selected,
        "total_characters": len(text),
    }


@router.get("/reading-map", response_model=ReadingMapPage)
async def get_reading_map(
    run_id: str, request: Request,
    version: str = Query(pattern=r"^[0-9a-f]{64}$"),
    include_hsi_tables: bool = False,
    offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=50),
) -> ReadingMapPage:
    detail, context = await _context(request, run_id, version, include_hsi_tables)
    return await run_blocking(reading_page, detail, context, offset=offset, limit=limit)


@router.get("/reading-map/units/{location_id}", response_model=ReadingUnitDetail)
async def get_reading_unit(
    run_id: str, location_id: str, request: Request,
    version: str = Query(pattern=r"^[0-9a-f]{64}$"), include_hsi_tables: bool = False,
    text_offset: int = Query(default=0, ge=0),
    text_length: int = Query(default=1600, ge=1, le=2400),
    anchor_offset: int = Query(default=0, ge=0),
    anchor_limit: int = Query(default=8, ge=1, le=12),
    fulltext_offset: int = Query(default=0, ge=0),
    fulltext_limit: int = Query(default=8, ge=1, le=12),
) -> ReadingUnitDetail:
    detail, context = await _context(request, run_id, version, include_hsi_tables)
    try:
        return await run_blocking(
            reading_unit, detail, context, location_id, text_offset=text_offset,
            text_length=text_length, anchor_offset=anchor_offset, anchor_limit=anchor_limit,
            fulltext_offset=fulltext_offset, fulltext_limit=fulltext_limit,
        )
    except KeyError as exc:
        raise HTTPException(404, "该版本中不存在所选正文位置") from exc


@router.post("/records", status_code=201)
async def create_record(run_id: str, body: AcceptanceRequest, request: Request) -> dict:
    detail = await _detail(request, run_id)
    settings = request.app.state.settings
    previous = await _storage_call(repeated_record, detail, settings.artifact_root, body)
    if previous is not None:
        return previous
    detail, context = await _context(
        request, run_id, body.document_version, body.include_hsi_tables
    )
    raw = request.headers.get("authorization", "").partition(" ")[2]
    return await _storage_call(
        save_record,
        detail,
        settings.artifact_root,
        settings.artifact_total_bytes,
        body,
        context,
        application_identity(),
        reviewer_id=principal_for(request).id,
        secrets=tuple(filter(None, (raw, request.headers.get("x-api-key", "")))),
    )


@router.get("/evidence/{evidence_id}")
async def get_evidence(
    run_id: str,
    evidence_id: str,
    request: Request,
    version: str = Query(pattern=r"^[0-9a-f]{64}$"),
    include_hsi_tables: bool = False,
    offset: int = Query(default=0, ge=0),
    length: int = Query(default=600, ge=1, le=1200),
) -> dict[str, Any]:
    _, context = await _context(request, run_id, version, include_hsi_tables)
    record = next((row for row in context["evidence"] if row["id"] == evidence_id), None)
    if record is None:
        raise HTTPException(404, "该版本中不存在所选证据")
    quote = context["_evidence_texts"][evidence_id]
    selected = quote[offset : offset + length]
    return {
        "document_version": version,
        "evidence": record,
        "offset": offset,
        "excerpt": safe_text(selected),
        "excerpt_redacted": safe_text(selected) != selected,
        "total_characters": len(quote),
    }


@router.get("/records")
async def get_records(
    run_id: str,
    request: Request,
    after: str = Query(default="", pattern=r"^(?:[0-9a-f]{64})?$"),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict:
    detail = await _detail(request, run_id)
    return await _storage_call(
        list_records, detail, request.app.state.settings.artifact_root, after, limit
    )


@router.get("/records/{record_id}")
async def get_record(run_id: str, record_id: str, request: Request) -> dict:
    detail = await _detail(request, run_id)
    return await _storage_call(
        read_record, detail, request.app.state.settings.artifact_root, record_id
    )


@router.get("/records/{record_id}/package")
async def download_package(run_id: str, record_id: str, request: Request) -> Response:
    record = await get_record(run_id, record_id, request)
    # The stored schema is already an allowlist of explicit selections; never
    # merge RunDetail, request_payload, model thoughts or source documents here.
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    return Response(
        payload,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="acceptance-{record["id"][:16]}.json"',
            "Cache-Control": "private, no-store",
            "X-Content-Version": record["document_version"],
            "X-Acceptance-SHA256": record["sha256"],
            "X-Content-SHA256": hashlib.sha256(payload).hexdigest(),
        },
    )
