"""Private, integrity-bound material for an explicit answer revision."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import Finding, Source
from .support import digest

PRIVATE_REVISION_TOOL = "_qa_revision_state"


class QaRevisionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    question: str
    contextual_query: str
    findings: list[Finding]
    sources: list[Source]
    origins: dict[str, str] = Field(default_factory=dict)
    scoped: bool = False
    scope_kind: Literal["paper", "research"] = "paper"
    scope_query: str = ""
    unresolved_topics: list[str] = Field(default_factory=list)
    admission_key: str = ""
    context: str | None = None
    draft: str = ""
    citations: list[str] = Field(default_factory=list)
    audit: dict[str, Any] = Field(default_factory=dict)
    snapshot_hash: str = ""

    def sealed(self) -> dict[str, Any]:
        value = self.model_dump(mode="json", exclude={"snapshot_hash"})
        return {**value, "snapshot_hash": digest(value)}


def read_revision_state(raw: Any) -> QaRevisionState:
    try:
        state = QaRevisionState.model_validate(raw)
        valid = (
            state.snapshot_hash == state.sealed()["snapshot_hash"]
            and bool(state.draft.strip() and state.findings and state.sources and state.citations)
            and len(state.citations) == len(set(state.citations))
            and all(finding.source_url in state.citations for finding in state.findings)
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError("修订快照缺失、损坏或不完整，未重新生成回答")
    return state


def stored_revision_state(thoughts: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (
            item.get("state")
            for item in reversed(thoughts)
            if item.get("tool") == PRIVATE_REVISION_TOOL
        ),
        None,
    )


def revision_availability(
    status: str,
    thoughts: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    if status != "fallback":
        return {"available": False}
    raw = stored_revision_state(thoughts)
    if raw is None:
        if evidence and any(
            item.get("unapproved_draft") for item in thoughts if item.get("tool") == "claim_check"
        ):
            return {"available": True}
        return {"available": False, "reason": "这条回答尚未保存完整的修订材料"}
    try:
        read_revision_state(raw)
    except ValueError as exc:
        return {"available": False, "reason": str(exc)}
    return {"available": True}


def legacy_revision_state(
    message: Any,
    *,
    sources: list[Source],
    scoped: bool = False,
    scope_kind: Literal["paper", "research"] = "paper",
    scope_query: str = "",
) -> dict[str, Any]:
    """Older messages must re-admit their original quotes; never invent a verdict."""
    record: dict[str, Any] = next(
        (
            item
            for item in reversed(message.thoughts)
            if item.get("tool") == "claim_check" and item.get("unapproved_draft")
        ),
        {},
    )
    frozen = list(sources)
    for item in message.thoughts:
        if item.get("tool") == "extraction_audit":
            frozen.extend(
                Source.model_validate(source) for source in item.get("audit", {}).get("sources", [])
            )
    frozen = list({source.model_dump_json(): source for source in frozen}.values())
    findings = [
        Finding(
            statement=item["statement"],
            source_url=item["source_url"],
            evidence_quote=item["evidence_quote"],
            confidence=0.0,
        )
        for item in message.evidence
    ]
    state = QaRevisionState(
        question=message.query,
        contextual_query="",
        findings=findings,
        sources=frozen,
        origins={item["source_url"]: item.get("origin", "web") for item in message.evidence},
        scoped=scoped,
        scope_kind=scope_kind,
        scope_query=scope_query,
        draft=record.get("unapproved_draft", ""),
        citations=list(message.citations),
        audit=record.get("review", {}),
        unresolved_topics=[
            topic
            for item in message.thoughts
            if item.get("tool") == "evidence_coverage"
            for topic in item.get("unresolved_topics", [])
        ],
    )
    return read_revision_state(state.sealed()).sealed()
