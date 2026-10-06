"""Private, sealed stages for an explicitly requested continuation.

An incomplete HTTP/model response is never a checkpoint. Recovery always creates
a new user-requested turn and only reuses completed, durably committed stages.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .qa_revision_state import QaRevisionState
from .support import digest

CheckpointWriter = Callable[[dict[str, Any]], Awaitable[None]]
MAX_CHECKPOINT_BYTES = 16 * 1024 * 1024


class QaCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    stage: Literal["evidence", "draft", "reviewed"]
    environment: str = ""
    execution_context: str = ""
    material: QaRevisionState
    thoughts: list[dict[str, Any]] = Field(default_factory=list)
    memory: dict[str, Any] | None = None
    progress: dict[str, Any] = Field(default_factory=dict)
    snapshot_hash: str = ""

    def sealed(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude={"snapshot_hash"})
        return {**data, "snapshot_hash": digest(data)}


def read_checkpoint(raw: Any) -> QaCheckpoint:
    try:
        state = QaCheckpoint.model_validate(raw)
        material = state.material
        valid = (
            state.snapshot_hash == state.sealed()["snapshot_hash"]
            and bool(material.sources and material.findings and material.admission_key)
            and (state.stage == "evidence" or bool(material.draft.strip() and material.citations))
            and (state.stage != "reviewed" or bool(material.audit))
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError("没有完整可复用的阶段结果，请重新提问")
    return state


def recovery_availability(status: str, payload: dict[str, Any]) -> dict[str, Any]:
    if status not in {"error", "cancelled"}:
        return {"available": False}
    try:
        checkpoint = read_checkpoint(payload.get("_checkpoint"))
    except ValueError as exc:
        return {"available": False, "reason": str(exc)}
    return {"available": True, "stage": checkpoint.stage}


def environment_hash(value: Any) -> str:
    # Settings may include credential/permission dataclasses; store only a hash.
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()
