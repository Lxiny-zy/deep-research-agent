"""Immutable acceptance selections in the existing per-run artifact control tree."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..persistence.repository import RunDetail
from .acceptance_context import next_work, safe_text, text_hash
from .acceptance_models import AcceptanceRequest
from .delivery_store import DeliveryConflict, _digest, _lock, delivery_store, version_registry


def record_id(run_id: str, request_id: str) -> str:
    return _digest(["acceptance", run_id, request_id])


def _path(identity: str) -> str:
    import re

    if not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise ValueError("invalid acceptance identifier")
    return f"acceptance/records/{identity}.json"


def _redact_selected_text(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, list):
        return [_redact_selected_text(item, secrets) for item in value]
    if isinstance(value, dict):
        text_fields = {"label", "locator", "section", "preview", "observation", "excerpt", "note"}
        return {
            key: safe_text(item, secrets)
            if key in text_fields and isinstance(item, str)
            else _redact_selected_text(item, secrets)
            for key, item in value.items()
        }
    return value


def read_record(detail: RunDetail, root: str, identity: str) -> dict[str, Any]:
    store, _ = delivery_store(detail, root)
    path = _path(identity)
    if not store.control_path(path).exists():
        raise FileNotFoundError("人工验收记录不存在")
    record = store.read_control_json(path)
    if record.get("id") != identity or record.get("run_id") != detail.id:
        raise ValueError("人工验收记录归属不一致")
    if record.get("sha256") != _digest({k: v for k, v in record.items() if k != "sha256"}):
        raise ValueError("人工验收记录校验失败")
    return record


def repeated_record(detail: RunDetail, root: str, body: AcceptanceRequest) -> dict | None:
    identity = record_id(detail.id, body.request_id)
    try:
        record = read_record(detail, root, identity)
    except FileNotFoundError:
        return None
    if record["request_hash"] != _digest(body.model_dump(mode="json")):
        raise DeliveryConflict("acceptance_request_conflict", "同一请求标识不能提交不同验收内容")
    return record


def save_record(
    detail: RunDetail,
    root: str,
    quota: int | None,
    body: AcceptanceRequest,
    context: dict[str, Any],
    application: dict[str, Any],
    *,
    reviewer_id: str,
    secrets: tuple[str, ...] = (),
) -> dict[str, Any]:
    store, _ = delivery_store(detail, root, quota)
    with _lock(store, "acceptance/write.lock"):
        previous = repeated_record(detail, root, body)
        if previous is not None:
            return previous
        if body.document_version != context["document_version"]:
            raise DeliveryConflict("document_version_changed", "文档版本已变化，请先核对目标版本")
        parent = read_record(detail, root, body.parent_record_id) if body.parent_record_id else None
        if parent and body.first_attempt_status not in {"unknown", parent["first_attempt_status"]}:
            raise DeliveryConflict(
                "acceptance_initial_status_conflict", "恢复记录不得改写已登记的首次结果"
            )
        delivery = None
        if body.delivery_version:
            delivery = version_registry(detail, root, body.delivery_version)
            if delivery["input_version"] != context["source_version"]:
                raise DeliveryConflict(
                    "acceptance_delivery_mismatch", "交付版本与所选文档不是同一输入"
                )
        locations = {item["id"]: item for item in context["locations"]}
        requirements = {item["id"]: item for item in context["requirements"]}
        sources = {item["id"]: item for item in context["sources"]}
        evidence = {item["id"]: item for item in context["evidence"]}
        selections = []
        for issue in body.issues:
            if issue.location_id and issue.location_id not in locations:
                raise DeliveryConflict("acceptance_location_mismatch", "问题位置不属于所选文档版本")
            if (
                not set(issue.requirement_ids) <= requirements.keys()
                or not set(issue.source_ids) <= sources.keys()
            ):
                raise DeliveryConflict("acceptance_scope_mismatch", "要求或材料不属于所选任务快照")
            original = context["_texts"].get(issue.location_id, "")
            if issue.excerpt and issue.excerpt not in original:
                raise DeliveryConflict(
                    "acceptance_excerpt_mismatch", "所选问题片段与该版本位置不一致"
                )
            location = locations.get(issue.location_id)
            selected_evidence = []
            for selected in issue.evidence_selections:
                raw_quote = context["_evidence_texts"].get(selected.evidence_id)
                if raw_quote is None or selected.excerpt not in raw_quote:
                    raise DeliveryConflict(
                        "acceptance_evidence_mismatch", "原文依据与所选证据版本不一致"
                    )
                selected_evidence.append(
                    {
                        **evidence[selected.evidence_id],
                        "excerpt": safe_text(selected.excerpt, secrets),
                        "excerpt_sha256": text_hash(selected.excerpt),
                        "excerpt_redacted": safe_text(selected.excerpt, secrets)
                        != selected.excerpt,
                    }
                )
            selections.append(
                {
                    "location": {k: v for k, v in location.items() if k != "preview"}
                    if location
                    else None,
                    "requirements": [
                        {
                            **requirements[key],
                            "locations": [
                                {k: v for k, v in locations[location_id].items() if k != "preview"}
                                for location_id in requirements[key]["location_ids"]
                            ],
                        }
                        for key in issue.requirement_ids
                    ],
                    "sources": [sources[key] for key in issue.source_ids],
                    "evidence": selected_evidence,
                    "excerpt": safe_text(issue.excerpt, secrets),
                    "excerpt_sha256": text_hash(issue.excerpt),
                    "excerpt_redacted": safe_text(issue.excerpt, secrets) != issue.excerpt,
                    "category": issue.category,
                    "observation": safe_text(issue.observation, secrets),
                    "conclusion": issue.conclusion,
                    "next_work": issue.next_work
                    or next_work(context["template"] or "", issue.category),
                    "todo_id": issue.todo_id,
                }
            )
        folder = store.control_path("acceptance/records")
        if folder.exists() and len(list(folder.glob("*.json"))) >= 500:
            raise DeliveryConflict("acceptance_limit", "本任务人工验收记录已达到 500 条上限")
        identity = record_id(detail.id, body.request_id)
        initial_status = parent["initial_status"] if parent else detail.status
        record = {
            "schema_version": 1,
            "id": identity,
            "run_id": detail.id,
            "request_id": body.request_id,
            "request_hash": _digest(body.model_dump(mode="json")),
            "created_at": datetime.now(UTC).isoformat(),
            "reviewer_id": reviewer_id,
            "application": application,
            "input_id": context["input_id"],
            "document_version": body.document_version,
            "source_version": context["source_version"],
            "include_hsi_tables": body.include_hsi_tables,
            "delivery_version": body.delivery_version,
            "delivery_status": delivery["status"] if delivery else None,
            "phase": body.phase,
            "parent_record_id": body.parent_record_id,
            "initial_record_id": parent["initial_record_id"] if parent else identity,
            "initial_status": initial_status,
            "initial_status_basis": "first_record_observation",
            "first_attempt_status": parent["first_attempt_status"]
            if parent
            else body.first_attempt_status,
            "first_attempt_status_basis": "user_reported"
            if (parent["first_attempt_status"] if parent else body.first_attempt_status)
            != "unknown"
            else "not_recorded",
            "observed_status": detail.status,
            "recovery_status": detail.status if parent else None,
            "conclusion": body.conclusion,
            "note": safe_text(body.note, secrets),
            "issues": selections,
            "privacy": {
                "collection": "explicit_selections_only",
                "full_document": False,
                "conversation_history": False,
                "credentials": False,
            },
        }
        record = _redact_selected_text(record, secrets)
        record["sha256"] = _digest(record)
        store.write_control_json(_path(identity), record)
        return record


def list_records(detail: RunDetail, root: str, after: str = "", limit: int = 50) -> dict:
    store, _ = delivery_store(detail, root)
    folder = store.control_path("acceptance/records")
    names = (
        sorted(path.stem for path in folder.glob("*.json") if path.stem > after)
        if folder.exists()
        else []
    )
    items = []
    for identity in names[:limit]:
        record = read_record(detail, root, identity)
        items.append(
            {
                key: record[key]
                for key in (
                    "id",
                    "created_at",
                    "document_version",
                    "phase",
                    "initial_record_id",
                    "parent_record_id",
                    "initial_status",
                    "observed_status",
                    "conclusion",
                )
            }
        )
    return {"items": items, "next_cursor": names[limit - 1] if len(names) > limit else None}
