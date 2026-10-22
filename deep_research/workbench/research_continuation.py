"""Version-bound follow-up research; never mutate a terminal parent run."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from ..config import Settings
from ..orchestration import WorkflowRun
from ..persistence.repository import RunDetail
from .content_revision import REVISION_SOURCES_KEY, effective_question, source_version
from .contract import CONTRACT_SCRATCH_KEY, contract_from_scratch
from .templates import get_template

CONTINUATION_KEY = "research_continuation"


def continuation_offer(detail: RunDetail, gates: list[Any]) -> dict[str, Any]:
    scratch = detail.orchestration.checkpoint.get("scratch", {}) if detail.orchestration else {}
    contract = contract_from_scratch(scratch)
    gaps: list[str] = []
    if isinstance(scratch.get("_research_incomplete"), list):
        gaps.extend(str(item) for item in scratch["_research_incomplete"])
    if contract is not None and contract.strategy != "none":
        from ..report.service import requires_corroboration
        from .coverage import coverage_gaps

        gaps.extend(coverage_gaps(
            scratch, detail.results, detail.query,
            require_corroboration=requires_corroboration(detail),
        ).gaps)
    for gate in gates:
        if gate.name == "citation" and gate.metrics.get("retrieval_shortfall", 0) > 0:
            gaps.extend(gate.issues)
        elif gate.name in {
            "source_processing", "provided_corpus", "review_coverage", "research_progress",
            "research_sufficiency",
        }:
            if gate.status != "pass":
                gaps.extend(gate.issues)
        elif gate.name == "scholarly":
            gaps.extend(
                item["message"] for item in gate.metrics.get("findings", [])
                if isinstance(item, dict) and item.get("code") == "recency-gap"
                and isinstance(item.get("message"), str)
            )
    reason = ""
    if detail.status not in {"done", "needs_review"}:
        reason = "请等待当前任务结束；执行故障请使用恢复。"
    elif contract is None or contract.template == "dataAnalysis":
        reason = "当前任务没有可继续研究的材料契约。"
    elif not gaps:
        reason = "没有需要补查或补读的证据缺口。"
    return {
        "available": not reason, "reason": reason, "gaps": list(dict.fromkeys(gaps)),
        "source_version": source_version(detail),
        "action": "read" if contract and contract.strategy == "none" else "retrieve",
    }


async def prepare_continuation(
    detail: RunDetail, settings: Settings, gaps: list[str]
) -> tuple[WorkflowRun, Settings]:
    from ..agents.base import Blackboard
    from ..orchestrator import create_initial_execution
    from ..report.service import requires_corroboration
    from ..workflow import Workflow
    from ..workflows import get_workflow
    from .extraction import processing_failures
    from .tiers import tier_overrides

    previous = detail.orchestration.checkpoint.get("scratch", {}) if detail.orchestration else {}
    contract = contract_from_scratch(previous)
    if contract is None:
        raise ValueError("缺少原任务契约")
    template = get_template(contract.template)
    if template is None or template.key == "dataAnalysis":
        raise ValueError("该任务不支持补充研究")
    # Closed-material work never silently broadens into open web search.
    strategy = (
        "none" if contract.strategy == "none" or "deep" not in template.strategies else "deep"
    )
    tier = "standard" if strategy == "none" else "deep"
    current = replace(
        settings, quality=contract.quality, research_tier=tier,
        require_corroboration=requires_corroboration(detail),
        **tier_overrides(tier, explicit={}, ceilings={}),
    )
    workflow = get_workflow(template.workflow_for(strategy))
    # Reuse already-read attachments for open research. Failed intake is
    # deliberately rerun when the requested operation is to repair reading.
    if strategy != "none" and not processing_failures(detail.results):
        workflow = workflow.model_copy(update={
            "steps": [step for step in workflow.steps if step.agent != "attachment_reader"]
        })
    name = f"continue_{workflow.name}"
    query = effective_question(detail)
    execution = create_initial_execution(query, name, current, requested_workflow=name)
    execution.definition = Workflow(
        name=name, description="复用已验证材料补足研究缺口", steps=workflow.steps
    ).model_dump(mode="json")
    scratch = execution.checkpoint["scratch"]
    for key in ("attachments", "project_id", "project_owner_id"):
        if key in previous:
            scratch[key] = deepcopy(previous[key])
    scratch[CONTRACT_SCRATCH_KEY] = contract.model_copy(update={
        "strategy": strategy, "tier": tier,
        "constraints": [*contract.constraints, "本次继续研究优先解决：" + "；".join(gaps)],
    }).model_dump(mode="json")
    scratch[CONTINUATION_KEY] = {
        "version": 1, "parent_run_id": detail.id,
        "source_version": source_version(detail), "gaps": gaps,
    }
    scratch[REVISION_SOURCES_KEY] = [s.model_dump(mode="json") for s in detail.sources]
    # Closed input workflows rebuild their coverage from the original inputs;
    # open workflows retain completed, audited research without failed batches.
    results = [] if strategy == "none" else [
        deepcopy(result) for result in detail.results if not processing_failures([result])
    ]
    execution.checkpoint = Blackboard(query=query, results=results, scratch=scratch).model_dump(
        mode="json"
    )
    return execution, current
