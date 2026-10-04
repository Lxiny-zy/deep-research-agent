"""Serialize only known renderer inputs; never persist executable callables."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from .models import Report, ResearchResult, Source
from .orchestration import WorkflowRun
from .persistence.repository import RunDetail
from .render_capacity import check_render_authority
from .render_queue import RenderJob
from .report.document import ReportDocument
from .workbench import delivery_store, publish
from .workbench.support import digest

RENDER_TASK_VERSION = 1
EXPORTS = {
    "md": "render_markdown",
    "csv": "render_csv",
    "xlsx": "render_xlsx",
    "pdf": "render_pdf",
    "tex": "render_latex",
    "bib": "render_bibtex",
    "bundle": "render_reproducibility_bundle",
    "paper-pdf": "render_latex_pdf",
}


def freeze_detail(detail: RunDetail) -> dict[str, Any]:
    checkpoint = detail.orchestration.checkpoint if detail.orchestration else {}
    return {
        "id": detail.id,
        "query": detail.query,
        "created_at": detail.created_at.isoformat() if detail.created_at else None,
        "report": detail.report.model_dump(mode="json") if detail.report else None,
        "results": [result.model_dump(mode="json") for result in detail.results],
        "sources": [source.model_dump(mode="json") for source in detail.sources],
        "workflow": detail.orchestration.workflow_name if detail.orchestration else None,
        "effective_query": checkpoint.get("query", detail.query),
        "scratch": {
            key: value
            for key, value in checkpoint.get("scratch", {}).items()
            if key not in publish._DELIVERY_RUNTIME_KEYS
        },
    }


def thaw_detail(value: dict[str, Any]) -> RunDetail:
    return RunDetail(
        id=value["id"],
        query=value["query"],
        status="done",
        created_at=datetime.fromisoformat(value["created_at"]) if value.get("created_at") else None,
        report=Report.model_validate(value["report"]) if value.get("report") else None,
        results=[ResearchResult.model_validate(item) for item in value.get("results", [])],
        sources=[Source.model_validate(item) for item in value.get("sources", [])],
        orchestration=WorkflowRun(
            workflow_name=value["workflow"],
            checkpoint={
                "query": value.get("effective_query", value["query"]),
                "scratch": value.get("scratch", {}),
            },
        )
        if value.get("workflow")
        else None,
    )


def bundle_payload(detail: RunDetail, quota: int | None = None) -> dict[str, Any]:
    return {
        "version": RENDER_TASK_VERSION,
        "detail": freeze_detail(detail),
        "input_version": publish.delivery_fingerprint(detail),
        "quota": quota,
    }


def export_payload(
    detail: RunDetail,
    document: ReportDocument,
    format: str,
    options: dict[str, Any],
    quota: int | None = None,
) -> dict[str, Any]:
    if format not in EXPORTS:
        raise ValueError("unknown report format")
    opts = dict(options)
    if "sources" in opts:
        opts["sources"] = [source.model_dump(mode="json") for source in opts["sources"]]
    if opts.get("manifest") is not None:
        opts["manifest"] = opts["manifest"].model_dump(mode="json")
    if format == "bundle":
        opts["run_id"] = detail.id
    scratch = detail.orchestration.checkpoint.get("scratch", {}) if detail.orchestration else {}
    return {
        "version": RENDER_TASK_VERSION,
        "detail": {
            "id": detail.id,
            "query": detail.query,
            "workflow": "export",
            "scratch": {"_artifact_slug": scratch.get("_artifact_slug")},
        },
        "format": format,
        "document": document.model_dump(mode="json"),
        "bibliography_presented": document._bibliography_presented,
        "options": opts,
        "quota": quota,
    }


def export_key(payload: dict[str, Any]) -> str:
    # Storage admission limits do not alter document bytes. A new limit may
    # reuse a previously completed export without allocating another copy.
    return digest({key: value for key, value in payload.items() if key != "quota"})


def _cached_export(job: RenderJob, root: str, quota: int | None) -> dict[str, Any] | None:
    store, _ = delivery_store.delivery_store(thaw_detail(job.payload["detail"]), root, quota)
    key = export_key(job.payload)
    if not store.control_path(f"render-jobs/{key}.json").exists():
        return None
    try:
        value = store.read_control_json(f"render-jobs/{key}.json")
    except FileNotFoundError:
        return None
    if not isinstance(value, dict) or value.get("render_hash") != key:
        raise ValueError("导出缓存与原请求不一致")
    path = store.control_path(value["path"])
    if path.stat().st_size != value["size"]:
        raise ValueError("导出缓存文件校验失败")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != value["sha256"] or len(data) != value["size"]:
        raise ValueError("导出缓存文件校验失败")
    return value


def progress_token(job: RenderJob, root: str) -> str:
    detail = thaw_detail(job.payload["detail"])
    store, _ = delivery_store.delivery_store(detail, root)
    if job.kind == "export":
        key = export_key(job.payload)
        if not store.control_path(f"render-jobs/{key}.json").exists():
            return digest(None)
        try:
            return digest(store.read_control_json(f"render-jobs/{key}.json"))
        except FileNotFoundError:
            return digest(None)
    index = delivery_store._index(store)
    source = job.payload["input_version"]
    current = index.get("current", {}).get(source, source)
    retry_id = "rq-" + job.id
    pending = (
        current
        if job.kind == "bundle"
        else delivery_store._digest([source, current, job.payload["format"], retry_id])
    )
    return digest(
        [
            current,
            index.get("pending", {}).get(pending),
            index.get("retry_requests", {}).get(retry_id),
        ]
    )


def execute_render(job: RenderJob, root: str, quota: int | None) -> dict[str, Any]:
    if job.payload.get("version") != RENDER_TASK_VERSION:
        raise ValueError("渲染请求版本不受支持")
    detail = thaw_detail(job.payload["detail"])
    if detail.id != job.run_id:
        raise ValueError("渲染请求不属于该任务")
    check_render_authority()
    if job.kind in {"bundle", "retry"}:
        if publish.delivery_fingerprint(detail) != job.payload["input_version"]:
            raise ValueError("渲染输入已改变")
        if job.kind == "bundle":
            bundle = delivery_store.build_or_load(detail, root, quota, publish.build_bundle)
        else:
            store, _ = delivery_store.delivery_store(detail, root, quota)
            request_id = "rq-" + job.id
            saved = delivery_store._index(store).get("retry_requests", {}).get(request_id)
            base = saved["base"] if saved else delivery_store.current_version(detail, root)
            if base is None:
                raise FileNotFoundError("交付版本不存在")
            if saved is None and base != job.payload["base_version"]:
                current = delivery_store.load_version(detail, root, base)
                if current.input_version != job.payload["input_version"]:
                    raise delivery_store.DeliveryConflict("delivery_source_changed", "定稿已变化")
                target = job.payload["format"]
                if not any(f["format"] == target and f.get("retryable") for f in current.failures):
                    if any(
                        file.format == target and file.status != "fail" for file in current.files
                    ):
                        return {"kind": "bundle", "version": current.content_version}
            bundle = delivery_store.retry_format(
                detail, root, quota, base, job.payload["format"], request_id
            )
        return {"kind": "bundle", "version": bundle.content_version}
    if job.kind != "export" or job.payload.get("format") not in EXPORTS:
        raise ValueError("unknown render operation")
    cached = _cached_export(job, root, quota)
    if cached is not None:
        return cached
    from . import report
    from .reproducibility import RunManifest

    document = ReportDocument.model_validate(job.payload["document"])
    document._bibliography_presented = job.payload.get("bibliography_presented") is True
    options = dict(job.payload.get("options", {}))
    if "sources" in options:
        options["sources"] = [Source.model_validate(source) for source in options["sources"]]
    if options.get("manifest") is not None:
        options["manifest"] = RunManifest.model_validate(options["manifest"])
    rendered = getattr(report, EXPORTS[job.payload["format"]])(document, **options)
    data = rendered.encode("utf-8") if isinstance(rendered, str) else rendered
    if not isinstance(data, bytes):
        raise ValueError("renderer returned an invalid result")
    store, _ = delivery_store.delivery_store(detail, root, quota)
    check_render_authority()
    key = export_key(job.payload)
    path = f"render-cache/{key}.bin"
    store.write_control_bytes(path, data)
    result = {
        "kind": "export",
        "render_hash": key,
        "path": path,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "text": isinstance(rendered, str),
    }
    check_render_authority()
    store.write_control_json(f"render-jobs/{key}.json", result)
    return result


def load_result(job: RenderJob, root: str, quota: int | None) -> Any:
    if job.result is None:
        raise ValueError("渲染任务没有保存结果")
    detail = thaw_detail(job.payload["detail"])
    if job.result.get("kind") == "bundle":
        bundle = delivery_store.load_version(detail, root, job.result["version"])
        if bundle.input_version != job.payload["input_version"]:
            raise ValueError("渲染结果与输入不一致")
        return bundle
    result = _cached_export(job, root, quota)
    if result is None:
        raise FileNotFoundError("导出结果不存在")
    store, _ = delivery_store.delivery_store(detail, root, quota)
    data = store.control_path(result["path"]).read_bytes()
    return data.decode("utf-8") if result["text"] else data


def error_record(exc: Exception) -> dict[str, Any]:
    from . import report
    from .artifacts import ArtifactError, ArtifactIntegrityError, ArtifactValidationError

    known = [
        getattr(report, name)
        for name in (
            "ChartDataError",
            "CsvTableNotFoundError",
            "CsvTableSelectionError",
            "XlsxDependencyError",
            "XlsxTableNotFoundError",
            "XlsxTableSelectionError",
            "PdfExportUnavailable",
            "PdfRenderError",
            "LatexExportUnavailable",
            "LatexRenderError",
        )
    ] + [
        delivery_store.DeliveryConflict,
        ArtifactIntegrityError,
        ArtifactValidationError,
        ArtifactError,
        FileNotFoundError,
        PermissionError,
        TimeoutError,
        OSError,
        ValueError,
    ]
    kind = next((cls.__name__ for cls in known if isinstance(exc, cls)), "RuntimeError")
    return {
        "kind": kind,
        "message": str(exc) if kind != "RuntimeError" else f"渲染失败（{type(exc).__name__}）",
        **({"code": exc.code} if isinstance(exc, delivery_store.DeliveryConflict) else {}),
    }


def raise_render_error(error: dict[str, Any]) -> None:
    from . import report
    from .artifacts import ArtifactError, ArtifactIntegrityError, ArtifactValidationError

    classes = {
        name: getattr(report, name)
        for name in (
            "ChartDataError",
            "CsvTableNotFoundError",
            "CsvTableSelectionError",
            "XlsxDependencyError",
            "XlsxTableNotFoundError",
            "XlsxTableSelectionError",
            "PdfExportUnavailable",
            "PdfRenderError",
            "LatexExportUnavailable",
            "LatexRenderError",
        )
    }
    classes.update(
        {
            cls.__name__: cls
            for cls in (
                ArtifactError,
                ArtifactIntegrityError,
                ArtifactValidationError,
                FileNotFoundError,
                PermissionError,
                TimeoutError,
                OSError,
                ValueError,
            )
        }
    )
    if error.get("kind") == "DeliveryConflict":
        raise delivery_store.DeliveryConflict(
            error.get("code", "delivery_changed"), error["message"]
        )
    if error.get("kind") == "recovery":
        raise TimeoutError(error.get("message", "渲染未取得进展"))
    raise classes.get(error.get("kind", ""), RuntimeError)(error.get("message", "渲染任务失败"))
