"""用本结果继续：把已完成调研或综述的已核验证据交给幻灯片、思维导图写作者。

派生任务是新任务，原任务保持不变。新任务只复用原任务已通过核验的发现与来源快照，
不重新检索；按目标模板的契约与验收门独立检查，缺口在新任务里如实报告。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from ..config import Settings
from ..orchestration import WorkflowRun
from ..persistence.repository import RunDetail
from .content_revision import (
    REVISION_SOURCES_KEY,
    WRITERS,
    content_state,
    effective_question,
    source_version,
)
from .contract import CONTRACT_SCRATCH_KEY, build_contract, contract_from_scratch
from .extraction import processing_failures
from .templates import get_template

DERIVATION_KEY = "derived_from"
# 原任务类型 → 可以直接生成的交付形式
DERIVABLE = {"autoResearch": ("slides", "mindmap"), "litReview": ("slides", "mindmap")}
# 与模板无关、随任务带走的资料库归属
_CARRIED = ("project_id", "project_owner_id")


def derivation_offer(detail: RunDetail, version: str | None = None) -> dict[str, Any]:
    """``version`` 可传入同一请求里已算好的 ``source_version``，免得对全部材料再求一次摘要。"""
    from ..guardrails import report_eligible
    from ..report.service import requires_corroboration

    contract = contract_from_scratch(content_state(detail))
    reason = ""
    if detail.status not in {"done", "needs_review"} or detail.report is None:
        reason = "请先完成当前任务。"
    elif contract is None:
        reason = "此历史任务缺少任务信息，不能直接生成。"
    elif contract.template not in DERIVABLE:
        reason = "只有课题调研和文献综述可以直接生成幻灯片或思维导图。"
    elif contract.strategy == "none":
        # 指定材料任务的验收要逐份核对原材料，派生任务拿不到这层契约
        reason = "仅用指定材料的任务，请用同一批材料新建幻灯片或思维导图任务。"
    elif processing_failures(detail.results):
        reason = "材料处理尚未完成，请先处理检索或抽取错误。"
    elif not any(
        report_eligible(finding, require_corroboration=requires_corroboration(detail))
        for result in detail.results
        for finding in result.findings
    ):
        reason = "没有已通过核验的研究证据。"
    return {
        "available": not reason,
        "reason": reason,
        "targets": [] if reason or contract is None else list(DERIVABLE[contract.template]),
        "source_version": version or source_version(detail),
    }


def prepare_derivation(
    detail: RunDetail, settings: Settings, target: str
) -> tuple[WorkflowRun, Settings]:
    from ..agents.base import Blackboard
    from ..orchestrator import create_initial_execution
    from ..report.service import requires_corroboration
    from ..workflow import Step, Workflow

    offer = derivation_offer(detail)
    if target not in offer["targets"]:
        raise ValueError(offer["reason"] or "该任务不能生成这种交付形式")
    state = content_state(detail)
    old = contract_from_scratch(state)
    template = get_template(target)
    assert old is not None and template is not None
    contract = build_contract(
        template,
        old.original_request,
        answers=old.confirmed_choices,
        tier=old.tier,
        strategy=old.strategy,
        quality=old.quality,
    )
    current = replace(
        settings, quality=old.quality, require_corroboration=requires_corroboration(detail)
    )
    writer = WRITERS[target]
    name = f"derived_{writer}"
    query = effective_question(detail)
    execution = create_initial_execution(query, name, current, requested_workflow=name)
    execution.definition = Workflow(
        name=name,
        description="复用原任务已核验的证据生成新的交付形式",
        steps=[Step(agent=writer)],
    ).model_dump(mode="json")
    scratch = execution.checkpoint["scratch"]
    scratch.update({key: deepcopy(state[key]) for key in _CARRIED if key in state})
    scratch[CONTRACT_SCRATCH_KEY] = contract.model_dump(mode="json")
    scratch[DERIVATION_KEY] = {
        "version": 1,
        "parent_run_id": detail.id,
        "source_version": offer["source_version"],
        "from_template": old.template,
        "template": target,
    }
    scratch[REVISION_SOURCES_KEY] = [source.model_dump(mode="json") for source in detail.sources]
    bb = Blackboard(query=query, results=deepcopy(detail.results), scratch=scratch)
    execution.checkpoint = bb.model_dump(mode="json")
    return execution, current


__all__ = ["DERIVABLE", "DERIVATION_KEY", "derivation_offer", "prepare_derivation"]
