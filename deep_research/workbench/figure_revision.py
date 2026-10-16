"""Converge optional figures without replaying paid edits or losing better versions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ..persistence.repository import LeaseLostError
from .figure_review import figure_signature
from .figures import ConceptFigure
from .support import SupportReviewer, digest
from .writing_progress import WritingProgressError

Revise = Callable[[ConceptFigure, dict[str, Any]], Awaitable[ConceptFigure]]
Save = Callable[[], Awaitable[None]]


def _issues(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        tuple(sorted(" ".join(issue.split()) for issue in record["issues"])),
        tuple(sorted(
            (item["unit_id"], item["verdict"], " ".join(item.get("reason", "").split()))
            for item in record.get("decisions", [])
            if item["verdict"] not in {"supported", "non_factual"}
        )),
    )


async def review_figure_with_revisions(
    figure: ConceptFigure,
    reviewer: SupportReviewer,
    revise: Revise,
    *,
    max_revisions: int,
    state: dict[str, Any],
    save: Save,
    on_progress: Callable[[str], None],
) -> tuple[ConceptFigure, dict[str, Any], dict[str, Any]]:
    from .figure_review import review_figure

    versions = state.setdefault("versions", [])
    if not versions:
        versions.append({"model": figure.model_dump(mode="json"), "record": None})
        await save()
    used = state.get("revisions_used", len(versions) - 1)
    if (
        type(used) is not int or not len(versions) - 1 <= used <= max_revisions
        or type(state.get("complete", False)) is not bool
    ):
        raise WritingProgressError("图示修订额度或停止记录无法恢复")
    state["revisions_used"] = used
    for index, version in enumerate(versions):
        record = version.get("record")
        if record is None and index == len(versions) - 1 and not state.get("complete"):
            continue
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("issues"), list)
            or any(not isinstance(issue, str) for issue in record["issues"])
            or record.get("status") not in {"pass", "fail"}
            or type(record.get("can_revise")) is not bool
            or record.get("input_hash") != figure_signature(
                ConceptFigure.model_validate(version["model"]), reviewer.evidence,
            )
        ):
            raise WritingProgressError("图示核验记录与已保存版本不一致")

    reason = state.get("stop_reason", "")
    while not state.get("complete"):
        current = ConceptFigure.model_validate(versions[-1]["model"])
        record = versions[-1]["record"]
        if record is None:
            on_progress("核对图示节点与关系…")
            record = await review_figure(current, reviewer)
            versions[-1]["record"] = record
            await save()
        unchanged = len(versions) > 1 and _issues(record) == _issues(versions[-2]["record"])
        reason = (
            "passed" if record["status"] == "pass"
            else "verification_incomplete" if not record["can_revise"]
            else "no_progress" if unchanged
            else "revision_limit" if used >= max_revisions
            else ""
        )
        if reason:
            break
        # Count before requesting an edit. A cancelled or failed edit retains
        # its charge on recovery, even when no new figure was produced.
        used += 1
        state["revisions_used"] = used
        await save()
        on_progress("修正图示中的节点与关系问题…")
        try:
            candidate = await revise(current, record)
        except (LeaseLostError, WritingProgressError):
            raise
        except Exception as exc:
            reason = "revision_failed"
            state["error_type"] = type(exc).__name__
            break
        model = candidate.model_dump(mode="json")
        if any(digest(model) == digest(version["model"]) for version in versions):
            reason = "no_progress"
            break
        versions.append({"model": model, "record": None})
        await save()

    chosen = min(
        range(len(versions)), key=lambda index: (len(versions[index]["record"]["issues"]), -index),
    )
    state.update(complete=True, stop_reason=reason, chosen=chosen + 1)
    await save()
    record = versions[chosen]["record"]
    return ConceptFigure.model_validate(versions[chosen]["model"]), record, {
        "attempts": used + 1,
        "generated_versions": len(versions),
        "chosen": chosen + 1,
        "remaining": record["issues"],
        "revisions_used": used,
        "budget_limit": max_revisions,
        "stop_reason": reason,
    }
