"""Durable HTTP receipts over the existing rendering queue."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from .. import render_tasks
from ..blocking import run_blocking
from ..render_queue import RenderJob, _hash
from ..render_service import service_for
from .delivery_store import (
    DeliveryConflict,
    _lock,
    current_version,
    delivery_store,
    version_registry,
)


class RenderOperationRequest(BaseModel):
    kind: Literal["bundle", "retry", "export"]
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    version: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    format: (
        Literal[
            "md",
            "html",
            "pdf",
            "docx",
            "pptx",
            "png",
            "csv",
            "xlsx",
            "tex",
            "bib",
            "bundle",
            "paper-pdf",
        ]
        | None
    ) = None
    include_hsi_tables: bool = False
    table_id: str | None = Field(default=None, min_length=1, max_length=128)
    profile: Literal["academic", "technical", "executive", "appendix"] = "academic"
    template: Literal["ctexart", "ctexrep", "ieeetran", "acmart"] = "ctexart"

    @model_validator(mode="after")
    def supported(self) -> RenderOperationRequest:
        if self.kind != "bundle" and (not self.version or not self.format):
            raise ValueError("version and format are required")
        if self.kind == "export" and self.format not in render_tasks.EXPORTS:
            raise ValueError("unsupported document export format")
        if self.kind == "retry" and self.format not in {
            "md",
            "html",
            "pdf",
            "docx",
            "pptx",
            "png",
            "xlsx",
        }:
            raise ValueError("unsupported delivery repair format")
        return self


def _alias_path(request_id: str) -> str:
    return f"render-operations/{render_tasks.digest(request_id)}.json"


def operation_alias(detail: Any, root: str, request_id: str) -> dict[str, Any] | None:
    store, _ = delivery_store(detail, root)
    path = _alias_path(request_id)
    if not store.control_path(path).exists():
        return None
    try:
        value = store.read_control_json(path)
    except FileNotFoundError:
        return None
    if value.get("request_id") != request_id or value.get("run_id") != detail.id:
        raise ValueError("渲染回执归属不一致")
    expected = render_tasks.digest({k: v for k, v in value.items() if k != "sha256"})
    if value.get("sha256") != expected:
        raise ValueError("渲染回执校验失败")
    return value


def bind_operation(
    detail: Any,
    root: str,
    specification: dict,
    key: str,
    quota: int | None,
) -> dict[str, Any]:
    store, _ = delivery_store(detail, root, quota)
    with _lock(store, "render-operations/requests.lock"):
        previous = operation_alias(detail, root, specification["request_id"])
        if previous is not None:
            if previous["specification"] != specification:
                raise DeliveryConflict("render_request_conflict", "同一操作标识不能用于不同输入")
            return previous
        value = {
            "run_id": detail.id,
            "request_id": specification["request_id"],
            "specification": specification,
            "key": key,
        }
        value["sha256"] = render_tasks.digest(value)
        store.write_control_json(_alias_path(specification["request_id"]), value)
        return value


def receipt(job: RenderJob, operation: dict[str, Any] | None = None) -> dict[str, Any]:
    operation = operation or {}
    version = operation.get("version") or job.payload.get("document", {}).get("content_version")
    result_version = (job.result or {}).get("version") if job.kind != "export" else version
    content_version = result_version or version
    base = f"/api/runs/{job.run_id}"
    query = "?" + urlencode({"request_id": operation["request_id"]}) if operation else ""
    result_url = None
    if job.status == "done":
        result_url = (
            f"{base}/render-operations/{job.id}/result"
            if job.kind == "export"
            else f"{base}/deliverables?{urlencode({'version': result_version})}"
        )
    return {
        "id": job.id,
        "operation_id": job.id,
        "request_id": operation.get("request_id"),
        "run_id": job.run_id,
        "kind": job.kind,
        "format": job.payload.get("format"),
        "status": job.status,
        "version": content_version,
        "content_version": content_version,
        "input_version": job.payload.get("input_version", version),
        "status_url": f"{base}/render-operations/{job.id}{query}",
        "result_url": result_url,
        "created_at": job.created_at,
        "attempts": job.attempts,
        "error": {"kind": job.error.get("kind"), "message": job.error.get("message")}
        if job.error
        else None,
    }


async def owned_job(request: Request, run_id: str, job_id: str) -> RenderJob:
    service = service_for(request.app.state.repo, request.app.state.settings)
    job = await service.queue.get(job_id)
    if job is None or job.run_id != run_id or job.pool != service.pool:
        raise HTTPException(404, "渲染操作不存在")
    if (
        job.payload.get("detail", {}).get("id") != run_id
        or job.payload_hash != _hash(job.pool, run_id, job.kind, job.payload)
    ):
        raise HTTPException(409, {
            "code": "delivery_integrity", "message": "渲染快照校验失败",
        })
    return job


async def submit(request: Request, run_id: str, body: RenderOperationRequest) -> dict[str, Any]:
    from ..http.auth import principal_for

    if body.kind != "export" and not principal_for(request).can_research:
        raise HTTPException(403, "当前身份为只读，无法重新生成交付物")
    repo, settings = request.app.state.repo, request.app.state.settings
    detail = await repo.get_run(run_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    service = service_for(repo, settings)
    specification = body.model_dump(mode="json")
    alias = await run_blocking(operation_alias, detail, service.root, body.request_id)
    if alias is not None:
        if alias["specification"] != specification:
            raise HTTPException(
                409,
                {
                    "code": "render_request_conflict",
                    "message": "同一操作标识不能用于不同输入",
                },
            )
        existing = await service.queue.by_key(alias["key"])
        if existing is not None:
            existing = await owned_job(request, run_id, existing.id)
            service.wake()
            return receipt(existing, specification)
    if body.kind in {"bundle", "retry"}:
        if (
            detail.status not in {"done", "needs_review", "error", "cancelled"}
            or detail.report is None
        ):
            raise HTTPException(409, "研究尚未定稿，不能生成交付物")
        payload = render_tasks.bundle_payload(detail, service.quota)
        payload["automatic"] = False
        if body.kind == "retry":
            active = await run_blocking(current_version, detail, service.root)
            if active != body.version:
                raise HTTPException(
                    409,
                    {
                        "code": "delivery_version_changed",
                        "message": "交付版本已变化",
                    },
                )
            registry = await run_blocking(version_registry, detail, service.root, body.version)
            if not any(
                f["format"] == body.format and f.get("retryable") for f in registry["failures"]
            ):
                raise HTTPException(
                    409,
                    {
                        "code": "delivery_not_retryable",
                        "message": "该格式无需修复或需要先修订正文",
                    },
                )
            payload.update(base_version=body.version, format=body.format)
        elif body.version is not None and body.version != payload["input_version"]:
            raise HTTPException(
                409,
                {
                    "code": "delivery_source_changed",
                    "message": "研究定稿已变化",
                },
            )
        key = render_tasks.digest(
            [service.pool, run_id, "bundle", payload["input_version"], False, service.quota]
            if body.kind == "bundle"
            else [service.pool, run_id, "retry-operation", body.version, body.format, payload]
        )
    else:
        from ..api import _enrich_run_detail, _require_supported_report
        from ..report.service import ReportService

        assert body.format is not None  # Validated by RenderOperationRequest.
        document = await ReportService(repo).document(
            run_id, include_hsi_tables=body.include_hsi_tables
        )
        from .publish import delivery_fingerprint

        if document.source_version != delivery_fingerprint(detail):
            raise HTTPException(409, {
                "code": "document_version_changed",
                "message": "导出期间研究定稿已变化，请刷新预览后再导出",
            })
        if document.content_version != body.version:
            raise HTTPException(
                409,
                {
                    "code": "document_version_changed",
                    "message": "报告内容已变化，请刷新预览后再导出",
                    "expected_version": body.version,
                    "current_version": document.content_version,
                },
            )
        if body.format in {"pdf", "paper-pdf"}:
            _require_supported_report(document)
        options: dict[str, Any] = {}
        if body.format in {"csv", "xlsx"}:
            options["table_id"] = body.table_id
        if body.format in {"tex", "paper-pdf", "bundle"}:
            options.update(profile=body.profile, template=body.template)
        if body.format in {"bib", "bundle"}:
            options["sources"] = detail.sources
        if body.format == "bundle":
            detail = await _enrich_run_detail(repo, detail)
            options["manifest"] = detail.manifest
        payload = render_tasks.export_payload(detail, document, body.format, options, service.quota)
        key = render_tasks.digest([service.pool, run_id, "export", payload])
    alias = await run_blocking(
        bind_operation, detail, service.root, specification, key, service.quota
    )
    if alias["key"] != key:
        raise HTTPException(
            409,
            {
                "code": "render_request_conflict",
                "message": "操作输入已变化，请使用新的操作标识",
            },
        )
    job = await service.reserve(
        body.kind, run_id, key, payload, restart_failed=True, request_token=body.request_id
    )
    return receipt(job, specification)
