"""One checkpointed repair round before publishing a needs-review decision."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import replace
from typing import Any

from ..blocking import run_rendering
from ..config import Settings
from ..execution_policy import transient_failure
from ..persistence.repository import LeaseLostError, RunDetail
from .completion import assess_completion
from .content_revision import _CONTENT_GATES, revision_offer
from .delivery_store import build_or_load, retry_format
from .prose_review import stored_review
from .publish import DeliveryBundle, build_bundle, delivery_fingerprint
from .support import digest
from .writing_progress import WritingProgressError

REPAIR_KEY = "_completion_repair"
Persist = Callable[[RunDetail, bool], Awaitable[None]]
Revise = Callable[[RunDetail], Awaitable[RunDetail]]


def _scratch(detail: RunDetail) -> dict[str, Any]:
    assert detail.orchestration is not None
    return detail.orchestration.checkpoint.setdefault("scratch", {})


def content_rank(detail: RunDetail) -> tuple[int, int, int]:
    scratch = _scratch(detail)
    extras = scratch.get("workbench", {}).get("extras", {})
    audit = stored_review(scratch) or extras.get("node_review") or {}
    issues = set(audit.get("issues", []))
    issues.update(extras.get("revision", {}).get("remaining", []))
    issues.update(audit.get("requirements_review", {}).get("issues", []))
    replaced = bool(scratch.get("_report_validation", {}).get("fallback"))
    text = detail.report.markdown if detail.report else ""
    return int(replaced or not text.strip()), len(issues), -len(text)


async def repair_completion(
    detail: RunDetail,
    settings: Settings,
    completion: dict[str, Any],
    *,
    revise: Revise,
    persist: Persist,
    emit: Callable[[str], Any],
    recover_transient: bool = False,
) -> tuple[RunDetail, dict[str, Any]]:
    if detail.orchestration is None:
        return detail, completion
    working = deepcopy(detail)
    state = _scratch(working).get(REPAIR_KEY)
    if state is not None:
        if not isinstance(state, dict) or state.get("version") != 1:
            raise WritingProgressError("自动修复记录无法识别")
        if state.get("status") == "complete":
            return working, completion
    elif completion["status"] == "done":
        return working, completion
    else:
        state = {
            "version": 1,
            "status": "running",
            "input_version": delivery_fingerprint(working),
            "content": {"attempts": 0, "status": "pending"},
            "formats": {},
        }
        _scratch(working)[REPAIR_KEY] = state

    async def save(changed: bool = False) -> None:
        _scratch(working)[REPAIR_KEY] = state
        await persist(working, changed)
        _scratch(working)[REPAIR_KEY] = state

    await save()

    async def bundle_for() -> DeliveryBundle:
        return await run_rendering(
            build_or_load,
            working,
            settings.artifact_root,
            settings.artifact_total_bytes,
            build_bundle,
        )

    bundle = await bundle_for()
    await run_rendering(assess_completion, working, bundle)
    content = state["content"]
    if content["status"] in {"pending", "running"}:
        # Use the existing eligibility rules without changing the actual run's status.
        offered = revision_offer(
            replace(working, status="needs_review"),
            [gate for gate in bundle.gates if gate.blocking_issues],
        )
        if offered["available"]:
            content.update(status="running", attempts=1)
            content.setdefault(
                "issues",
                [
                    issue
                    for gate in bundle.gates
                    if gate.name in _CONTENT_GATES
                    for issue in gate.blocking_issues or []
                ],
            )
            await save()
            emit("交付仍有阻断内容，自动尝试一轮定向修订…")
            try:
                candidate = await revise(deepcopy(working))
            except (LeaseLostError, WritingProgressError):
                raise
            except Exception as exc:
                if recover_transient and transient_failure(exc):
                    await save()
                    raise
                content["error"] = type(exc).__name__
                content["status"] = "failed"
                await save()
            else:
                improved = content_rank(candidate) < content_rank(working)
                if improved:
                    working = candidate
                    _scratch(working)[REPAIR_KEY] = state
                content.update(status="complete", improved=improved)
                await save(improved)
                bundle = await bundle_for()
        else:
            content.update(status="skipped", reason=offered["reason"])
            await save()

    planned = state.get("format_targets")
    if planned is None:
        planned = sorted(
            {failure["format"] for failure in bundle.failures if failure.get("retryable")}
        )
        state["format_targets"] = planned
        await save()
    for target in planned:
        attempt = state["formats"].get(target)
        if attempt is None:
            attempt = {
                "status": "running",
                "base_version": bundle.content_version,
                "request_id": "auto-" + digest([detail.id, state["input_version"], target])[:59],
            }
            state["formats"][target] = attempt
            await save()
        if attempt["status"] != "running":
            continue
        emit(f"自动重试 {target.upper()} 交付文件…")
        bundle = await run_rendering(
            retry_format,
            working,
            settings.artifact_root,
            settings.artifact_total_bytes,
            attempt["base_version"],
            target,
            attempt["request_id"],
        )
        attempt.update(status="complete", content_version=bundle.content_version)
        await save()
    state["status"] = "complete"
    await save()
    return working, await run_rendering(assess_completion, working, bundle)
