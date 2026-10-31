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
from .provider_limits import ProviderBusyError

COMMITTED_RESEARCH_PROGRESS_KEY = "_committed_research_progress"


class TaskYield(RuntimeError):
    """A durable stage boundary voluntarily releases the execution slot."""


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


# 学术工作流必须命中学术源：只有 openalex / arxiv 提供 DOI、机构、撤稿标记与开放
# 全文位置，而全文解析、数值校验与「同一 work」独立性判定都依赖这些字段。二者均
# 无需 API key，因此补齐不引入新的凭据要求。
_SCHOLARLY_SEARCH_BACKENDS = ("openalex", "arxiv")
# 面向文献的工作台任务（调研 / 综述 / 汇报 / 知识导图）的证据应首选论文，而不是通用网页。
_SCHOLARLY_WORKFLOWS = frozenset(
    {
        "hsi_review",
        "research",
        "research_quick",
        "lit_review",
        "lit_review_quick",
        "slides",
        "slides_deep",
        "mindmap",
        "mindmap_deep",
    }
)


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


def with_scholarly_backends(settings: Settings, workflow: str | None) -> Settings:
    """Ensure a scholarly workflow actually queries the scholarly backends.

    The intent policies declare ``source_strategy="scholarly_multi_source"``, but
    that string only reaches the Planner prompt; nothing downstream selects a
    backend from it.  Without this, selecting ``hsi_review`` under the default
    ``("tavily",)`` configuration silently runs an abstract-only web search and
    ``fulltext_enabled`` has nothing to act on.

    Backends the operator already configured are preserved and kept first, so an
    explicit ``DR_SEARCH_BACKENDS`` still decides priority.  Only the missing
    scholarly backends are appended.  Selected search profiles replace
    ``search_backends`` at run time, so they are extended the same way; otherwise
    a deployment with profiles never reaches a scholarly source.
    """
    if workflow not in _SCHOLARLY_WORKFLOWS:
        return settings
    backends = tuple(settings.search_backends)
    profiles = tuple(settings.search_profile_ids)
    return replace(
        settings,
        search_backends=backends
        + tuple(name for name in _SCHOLARLY_SEARCH_BACKENDS if name not in backends),
        search_profile_ids=profiles
        + tuple(
            f"builtin:{name}"
            for name in _SCHOLARLY_SEARCH_BACKENDS
            if profiles and f"builtin:{name}" not in profiles
        ),
    )


def resolved_settings(settings: Settings, workflow: str | None) -> Settings:
    return with_scholarly_backends(
        replace(settings, max_run_seconds=attempt_seconds(settings, workflow)), workflow
    )


def start_window(execution: WorkflowRun, seconds: int, total_seconds: int) -> None:
    """Start only after admission; a crash never silently resets an existing clock."""
    scratch = execution.checkpoint.setdefault("scratch", {})
    now = time.time()
    scratch.setdefault("_deadline_at", now + seconds)
    scratch.setdefault("_task_deadline_at", now + total_seconds)
    waiting = scratch.get("_resource_wait")
    if isinstance(waiting, dict):
        waiting["not_before"] = 0
        waiting["resumed_at"] = now


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
    """Known transport failures and backpressure; quality/auth errors stay terminal."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(
            current,
            (TaskYield, ProviderBusyError, TimeoutError, httpx.TransportError, APIConnectionError),
        ):
            return True
        if isinstance(current, APIStatusError):
            return current.status_code in {408, 409, 429} or current.status_code >= 500
        if isinstance(current, httpx.HTTPStatusError):
            return current.response.status_code in {408, 429, 500, 502, 503, 504}
        current = current.__cause__ or current.__context__
    return False


def resource_busy(error: BaseException) -> ProviderBusyError | None:
    """Find local admission backpressure even when a role wraps the exception."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ProviderBusyError):
            return current
        current = current.__cause__ or current.__context__
    return None


def resource_wait_checkpoint(
    execution: WorkflowRun, settings: Settings, *, retry_after: float, elapsed: float,
    reason: str = "provider_busy",
) -> tuple[WorkflowRun | None, str]:
    """Release a run's slot without charging a failure or no-progress attempt."""
    updated = execution.model_copy(deep=True)
    scratch: dict[str, Any] = updated.checkpoint.setdefault("scratch", {})
    now = time.time()
    deadline = scratch.get("_task_deadline_at", now + settings.max_task_seconds)
    if now >= deadline:
        return None, "task_deadline_exceeded"
    previous = scratch.get("_resource_wait", {})
    scratch["_resource_wait"] = {
        "count": int(previous.get("count", 0)) + 1,
        "reason": reason,
        "scheduled_at": now,
        # Wake at the final deadline to settle the task without another request.
        "not_before": min(deadline, now + max(1, retry_after)),
        "retry_after": retry_after,
    }
    scratch["_attempt_elapsed_origin"] = elapsed
    scratch.setdefault("_runtime_metrics", {})["elapsed"] = elapsed
    if reason != "stage_yield":
        scratch.pop("_deadline_at", None)
    return updated, "scheduled"


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
