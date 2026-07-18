"""Execution state models shared by workflow definitions and runtime adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


_STEP_TRANSITIONS: dict[StepStatus, set[StepStatus]] = {
    StepStatus.PENDING: {StepStatus.READY, StepStatus.SKIPPED, StepStatus.CANCELLED},
    StepStatus.READY: {StepStatus.RUNNING, StepStatus.SKIPPED, StepStatus.CANCELLED},
    StepStatus.RUNNING: {
        StepStatus.SUCCEEDED,
        StepStatus.FAILED,
        StepStatus.RETRYING,
        StepStatus.CANCELLED,
    },
    StepStatus.RETRYING: {StepStatus.RUNNING, StepStatus.CANCELLED},
    StepStatus.SUCCEEDED: set(),
    StepStatus.FAILED: set(),
    StepStatus.SKIPPED: set(),
    StepStatus.CANCELLED: set(),
}


class StepRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    node_id: str
    label: str
    kind: str
    agent: str = ""
    status: StepStatus = StepStatus.PENDING
    attempt: int = 0
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def transition(self, target: StepStatus, *, error: str | None = None) -> None:
        if target not in _STEP_TRANSITIONS[self.status]:
            raise ValueError(f"invalid step transition: {self.status} -> {target}")
        self.status = target
        if target == StepStatus.RUNNING:
            self.attempt += 1
            self.started_at = utcnow()
        if target in {
            StepStatus.SUCCEEDED,
            StepStatus.FAILED,
            StepStatus.SKIPPED,
            StepStatus.CANCELLED,
        }:
            self.finished_at = utcnow()
        self.error = error


class WorkflowRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    workflow_name: str
    status: RunStatus = RunStatus.PENDING
    input: dict = Field(default_factory=dict)
    output: dict = Field(default_factory=dict)
    definition: dict = Field(default_factory=dict)
    checkpoint: dict = Field(default_factory=dict)
    steps: list[StepRun] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def start(self) -> None:
        if self.status != RunStatus.PENDING:
            raise ValueError(f"cannot start workflow in state {self.status}")
        self.status = RunStatus.RUNNING
        self.started_at = utcnow()

    def finish(self, status: RunStatus, *, output: dict | None = None) -> None:
        if self.status != RunStatus.RUNNING:
            raise ValueError(f"cannot finish workflow in state {self.status}")
        if status not in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            raise ValueError(f"invalid terminal workflow status: {status}")
        self.status = status
        self.finished_at = utcnow()
        if output is not None:
            self.output = output
