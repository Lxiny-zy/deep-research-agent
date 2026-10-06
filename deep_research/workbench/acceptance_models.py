"""Explicit, bounded user selections for manual acceptance records."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NextWork = Literal["N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9", "N10", "N11"]


class EvidenceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=128)
    excerpt: str = Field(min_length=1, max_length=1200)


class AcceptanceIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    requirement_ids: list[str] = Field(default_factory=list, max_length=20)
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    evidence_selections: list[EvidenceSelection] = Field(default_factory=list, max_length=10)
    excerpt: str = Field(default="", max_length=1200)
    category: Literal[
        "correctness",
        "coverage",
        "citation",
        "format",
        "layout",
        "interaction",
        "context",
        "performance",
        "other",
    ]
    observation: str = Field(min_length=1, max_length=1000)
    conclusion: Literal["pending", "pass", "fail", "uncertain"] = "pending"
    next_work: list[NextWork] = Field(default_factory=list, max_length=11)
    todo_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9._/-]+$")

    @model_validator(mode="after")
    def selected_location(self) -> AcceptanceIssue:
        if self.excerpt and not self.location_id:
            raise ValueError("an excerpt requires a document location")
        if (
            not self.location_id
            and not self.requirement_ids
            and not self.source_ids
            and not self.evidence_selections
        ):
            raise ValueError("select a location, requirement or material identifier")
        return self


class AcceptanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    document_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    delivery_version: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    include_hsi_tables: bool = False
    phase: Literal["initial", "recovery"] = "initial"
    first_attempt_status: Literal["unknown", "done", "needs_review", "error", "cancelled"] = (
        "unknown"
    )
    parent_record_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    conclusion: Literal["pending", "pass", "fail", "mixed", "uncertain"] = "pending"
    issues: list[AcceptanceIssue] = Field(default_factory=list, max_length=30)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def recovery_parent(self) -> AcceptanceRequest:
        if (self.phase == "recovery") != bool(self.parent_record_id):
            raise ValueError("only a recovery record must reference an earlier record")
        return self
