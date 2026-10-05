"""Task deadlines and bounded recovery, frozen into each execution checkpoint."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError

from .config import Settings
from .orchestration import WorkflowRun

COMMITTED_RESEARCH_PROGRESS_KEY = "_committed_research_progress"


def committed_research_progress(scratch: dict[str, Any]) -> dict[str, str]:
    """Read fingerprints written only after durable subquestion results are verified."""
    raw = scratch.get(COMMITTED_RESEARCH_PROGRESS_KEY)
    if not isinstance(raw, dict):
        return {}

    def digest(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    return {key: value for key, value in raw.items() if digest(key) and digest(value)}


def attempt_seconds(settings: Settings, workflow: str | None) -> int:
    if settings.max_run_seconds:
        return settings.max_run_seconds
    name = workflow or "deep"
    task_tier = f"{name}:{settings.research_tier}"
    if task_tier in settings.run_timeout_profiles:
        return settings.run_timeout_profiles[task_tier]
    if name in settings.run_timeout_profiles:
        return settings.run_timeout_profiles[name]
    if name in {"qa", "paper_qa"}:
        tier, default = "qa", 600
    elif settings.research_tier:
        tier = "quick" if settings.research_tier == "light" else settings.research_tier
        default = {"quick": 7200, "standard": 14400, "deep": 43200}[tier]
    elif name == "quick" or name.endswith("_quick"):
        tier, default = "quick", 7200
    elif name in {"deep", "auto", "research", "hsi_review"} or name.endswith("_deep"):
        tier, default = "deep", 43200
    else:
        tier, default = "standard", 14400
    return settings.run_timeout_profiles.get(tier, default)


def resolved_settings(settings: Settings, workflow: str | None) -> Settings:
    return replace(settings, max_run_seconds=attempt_seconds(settings, workflow))


def start_window(execution: WorkflowRun, seconds: int, total_seconds: int) -> None:
    """Start only after admission; a crash never silently resets an existing clock."""
    scratch = execution.checkpoint.setdefault("scratch", {})
    now = time.time()
    scratch.setdefault("_deadline_at", now + seconds)
    scratch.setdefault("_task_deadline_at", now + total_seconds)


def progress_fingerprint(execution: WorkflowRun) -> str:
    checkpoint = execution.checkpoint
    # Operational timestamps, token counters and failure messages are not progress.
    payload = {
        "plan": checkpoint.get("plan"),
        "results": checkpoint.get("results"),
        "report": checkpoint.get("report"),
        "steps": [s.id for s in execution.steps if str(s.status) == "succeeded"],
        "research": committed_research_progress(checkpoint.get("scratch", {})),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def transient_failure(error: BaseException) -> bool:
    """Only known transport failures; quality/input/authentication errors stay terminal."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TimeoutError, httpx.TransportError, APIConnectionError)):
            return True
        if isinstance(current, APIStatusError):
            return current.status_code in {408, 409, 429} or current.status_code >= 500
        if isinstance(current, httpx.HTTPStatusError):
            return current.response.status_code in {408, 429, 500, 502, 503, 504}
        current = current.__cause__ or current.__context__
    return False


def recovery_checkpoint(
    execution: WorkflowRun, settings: Settings, *, reason: str, elapsed: float
) -> tuple[WorkflowRun | None, str]:
    updated = execution.model_copy(deep=True)
    scratch: dict[str, Any] = updated.checkpoint.setdefault("scratch", {})
    state = scratch.get("_recovery", {})
    now = time.time()
    if now >= scratch.get("_task_deadline_at", now + settings.max_task_seconds):
        return None, "task_deadline_exceeded"
    count = int(state.get("count", 0))
    if count >= settings.max_run_recoveries:
        return None, "recovery_limit_exceeded"
    fingerprint = progress_fingerprint(updated)
    stalled = int(state.get("no_progress", 0)) + 1 if state.get("progress") == fingerprint else 0
    if stalled >= settings.max_no_progress_attempts:
        return None, "no_progress"
    delay = min(60, 2 ** (count + 1))
    scratch["_recovery"] = {
        "count": count + 1,
        "no_progress": stalled,
        "progress": fingerprint,
        "reason": reason,
        "scheduled_at": now,
        "not_before": now + delay,
    }
    scratch["_attempt_elapsed_origin"] = elapsed
    metrics = scratch.setdefault("_runtime_metrics", {})
    metrics["elapsed"] = elapsed
    # Queue/cooldown time must not consume the next attempt window.
    scratch.pop("_deadline_at", None)
    return updated, "scheduled"
