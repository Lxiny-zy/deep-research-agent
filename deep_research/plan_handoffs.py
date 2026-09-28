"""Dependency-scoped, bounded context and durable external-step receipts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from .artifacts import ArtifactStore, ArtifactValidationError
from .plan_contract import TEXT_FORMATS, split_path
from .planning import validate_identifier

MAX_CONTEXT_CHARS = 24_000


def journal_path(slug: str, step_id: str) -> str:
    validate_identifier(slug, field_name="slug")
    validate_identifier(step_id, field_name="step id")
    return f"steps/{slug}/{step_id}.json"


def read_journal(store: ArtifactStore, slug: str, step_id: str) -> dict[str, Any]:
    path = journal_path(slug, step_id)
    # Absence is normal. Corrupt or inaccessible state must remain visible.
    if not store.control_path(path).exists():
        return {}
    return store.read_control_json(path)


def dependency_context(
    store: ArtifactStore, slug: str, metadata: Mapping[str, Any], scratch: Mapping[str, Any]
) -> str:
    dependencies = metadata.get("handoff_steps", [])
    if not dependencies:
        return ""
    records = {record.path: record for record in store.list_artifacts(slug)}
    plan = scratch.get("_execution_plan", {})
    states = {step["id"]: step for step in plan.get("steps", [])} if isinstance(plan, dict) else {}
    summaries: list[dict[str, Any]] = []
    selected: dict[str, Any] = {}
    for dependency in dependencies:
        step_id = dependency["id"]
        journal = read_journal(store, slug, step_id)
        saved = states.get(step_id, {})
        status = journal.get("status") or saved.get("status", "unknown")
        info: dict[str, Any] = {"step_id": step_id, "status": status}
        if journal:
            for key in ("summary", "gaps", "next_actions", "error"):
                if journal.get(key):
                    info[key] = journal[key]
        elif saved.get("metadata", {}).get("gap_note"):
            info["gaps"] = [saved["metadata"]["gap_note"]]
        specs = dependency.get("outputs", [])
        if journal.get("status") in {"done", "partial"}:
            # A prior failed attempt can leave files on disk, never evidence.
            committed = {item["path"]: item for item in journal.get("artifacts", [])}
        elif journal:
            committed = {}
        else:
            committed = None
        for spec in specs:
            path = spec["path"]
            split_path(path, slug)
            record = records.get(path)
            available = record is not None and (committed is None or path in committed)
            if not available:
                if spec.get("required", True) and status not in {
                    "partial",
                    "failed",
                    "interrupted",
                    "skipped",
                    "cancelled",
                }:
                    raise ArtifactValidationError(
                        f"required dependency artifact is missing: {path}"
                    )
                info.setdefault("gaps", []).append(f"Unavailable artifact: {path}")
                continue
            assert record is not None
            expectation = committed[path] if committed is not None else record.to_dict()
            store.verify(
                path,
                expected_sha256=expectation["sha256"],
                expected_size=expectation["size_bytes"],
                raise_on_error=True,
            )
            selected[path] = record
        summaries.append(info)

    # Reserve space for status/path metadata too. Fail explicitly if the
    # dependency index alone is too large, rather than silently hiding gaps.
    excerpts: list[dict[str, Any]] = []
    for path, record in selected.items():
        suffix = PurePosixPath(path).suffix.lstrip(".").lower()
        entry: dict[str, Any] = {"path": path, "sha256": record.sha256}
        if suffix not in TEXT_FORMATS:
            entry["note"] = "Binary artifact: content not injected; use a registered conversion."
        else:
            entry.update(content="", truncated=False)
        excerpts.append(entry)
    payload = {"steps": summaries, "artifacts": excerpts}
    remaining = MAX_CONTEXT_CHARS - len(json.dumps(payload, ensure_ascii=False))
    if remaining < 0:
        raise ArtifactValidationError(
            "dependency artifact index exceeds context budget; declare compact handoff files"
        )
    text_entries = [item for item in excerpts if "content" in item]
    allowance = remaining // max(1, len(text_entries))
    for entry in text_entries:
        try:
            with store.open(entry["path"], "r", encoding="utf-8") as handle:
                excerpt = handle.read(allowance + 1)
        except UnicodeError as exc:
            raise ArtifactValidationError(
                f"dependency artifact is not UTF-8: {entry['path']}"
            ) from exc
        content = excerpt[:allowance]
        # Count escaped newlines/control characters against the same budget.
        while len(json.dumps(content, ensure_ascii=False)) - 2 > allowance:
            encoded_size = len(json.dumps(content, ensure_ascii=False)) - 2
            content = content[: len(content) * allowance // encoded_size]
        entry["content"] = content
        entry["truncated"] = len(excerpt) > len(content)
    return json.dumps(payload, ensure_ascii=False)
