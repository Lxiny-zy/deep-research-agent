"""Create an independent revision run from frozen task material and failed drafts."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..config import Settings
from ..models import Report
from ..orchestration import WorkflowRun
from ..persistence.repository import RunDetail
from .contract import CONTRACT_SCRATCH_KEY, build_contract, contract_from_scratch
from .extraction import processing_failures
from .support import digest
from .templates import get_template

if TYPE_CHECKING:
    from ..agents.base import Blackboard

REVISION_KEY = "content_revision"
REVISION_SOURCES_KEY = "_revision_source_snapshots"
WRITERS = {
    "autoResearch": "research_writer",
    "litReview": "survey_writer",
    "paperRead": "paper_reader",
    "peerReview": "peer_reviewer",
    "dataAnalysis": "data_analyst",
    "slides": "slide_writer",
    "mindmap": "mindmap_writer",
}
_CONTENT_KEYS = {
    CONTRACT_SCRATCH_KEY,
    "attachments",
    "intake_sources",
    "paper_sources",
    "paper_abstracts",
    "analysis",
    "review_coverage",
    "workbench",
    "prose_review",
    "_report_validation",
    "intent",
    "intent_slots",
    "intent_route",
    "project_id",
    "project_owner_id",
}
_CONTENT_GATES = {
    "task_content",
    "prose_evidence",
    "node_evidence",
    "structure",
    "revision",
    "provided_corpus",
    "review_coverage",
    "scholarly",
    "citations",
    "length",
}


def content_state(detail: RunDetail) -> dict[str, Any]:
    scratch = detail.orchestration.checkpoint.get("scratch", {}) if detail.orchestration else {}
    if not isinstance(scratch, dict):
        return {}
    return {key: value for key, value in scratch.items() if key in _CONTENT_KEYS}


def effective_question(detail: RunDetail) -> str:
    question = detail.orchestration.checkpoint.get("query") if detail.orchestration else None
    if isinstance(question, str) and question.strip():
        return question
    contract = contract_from_scratch(content_state(detail))
    return (
        (detail.report.query if detail.report else "")
        or (contract.original_request if contract else "")
        or detail.query
    )


def source_version(detail: RunDetail) -> str:
    return digest(
        {
            "version": 2,
            "query": detail.query,
            "effective_query": effective_question(detail),
            "project_id": detail.project_id,
            "content": content_state(detail),
            "report": detail.report.model_dump(mode="json") if detail.report else None,
            "results": [r.model_dump(mode="json") for r in detail.results],
            "sources": [s.model_dump(mode="json") for s in detail.sources],
        }
    )


def revision_offer(detail: RunDetail, gates: list[Any]) -> dict[str, Any]:
    from ..guardrails import report_eligible
    from ..report.service import requires_corroboration

    contract = contract_from_scratch(content_state(detail))
    reason = ""
    if detail.status != "done" or detail.report is None:
        reason = "请先完成当前任务；执行中断时可使用任务恢复。"
    elif contract is None or contract.template not in WRITERS:
        reason = "此历史任务缺少继续修订所需的任务信息。"
    elif processing_failures(detail.results):
        reason = "材料处理尚未完成，请先处理检索或抽取错误。"
    elif contract.template == "dataAnalysis" and not (contract.dataset_csv or contract.demo_data):
        reason = "没有保留可复用的原始数据。"
    elif contract.template != "dataAnalysis" and not any(
        report_eligible(f, require_corroboration=requires_corroboration(detail))
        for result in detail.results
        for f in result.findings
    ):
        reason = "没有已通过材料核验的研究证据，请先补充材料。"
    elif not any(
        g.name in _CONTENT_GATES
        and (
            g.status == "fail"
            or g.status == "warn"
            and g.name
            in {
                "structure",
                "revision",
                "prose_evidence",
                "node_evidence",
                "review_coverage",
            }
        )
        for g in gates
    ):
        reason = "正文检查已通过；文件生成失败时可按格式重试。"
    return {"available": not reason, "reason": reason, "source_version": source_version(detail)}


def revision_seed(bb: Blackboard) -> tuple[Report | None, dict[str, Any] | None]:
    if not isinstance(bb.scratch.get(REVISION_KEY), dict):
        return None, None
    extras = bb.scratch.get("workbench", {}).get("extras", {})
    if isinstance(extras.get("unapproved_draft"), str) and extras["unapproved_draft"].strip():
        return (
            Report(
                query=bb.query,
                markdown=extras["unapproved_draft"],
                citations=list(bb.report.citations) if bb.report else [],
            ),
            extras.get("unapproved_draft_review"),
        )
    from .prose_review import stored_review

    return bb.report, stored_review(bb.scratch)


async def _refresh_missing_abstracts(scratch: dict[str, Any], settings: Settings) -> None:
    """Reparse only locally retained originals; never refetch a paper or redo extraction."""
    from ..blocking import run_blocking
    from .attachments import attachments_from_scratch, load_original, parse_attachment
    from .intake import PAPER_SOURCES_KEY
    from .paper_abstract import _candidates, abstract_span, prepare_abstracts

    if await prepare_abstracts(scratch, screen_intent=False):
        return
    recovered: list[dict[str, Any]] = []
    for attachment in attachments_from_scratch(scratch):
        if attachment.kind != "pdf" or not attachment.stored:
            continue
        raw = await run_blocking(load_original, settings, attachment.id)
        if raw is None or len(raw) != attachment.size:
            continue
        if hashlib.sha256(raw).hexdigest()[:24] != attachment.id:
            continue
        parsed = await parse_attachment(raw, attachment.filename, attachment.mime_type)
        for source, _parts in _candidates(parsed.sources()):
            if abstract_span(source) is not None:
                recovered.append(
                    source.model_copy(
                        update={
                            "url": f"https://workspace.invalid/attachments/{attachment.id}"
                            f"?abstract-reparse={len(recovered) + 1}",
                        }
                    ).model_dump(mode="json")
                )
    if recovered:
        scratch[PAPER_SOURCES_KEY] = [*scratch.get(PAPER_SOURCES_KEY, []), *recovered]
        await prepare_abstracts(scratch, screen_intent=False)


async def prepare_revision(detail: RunDetail, settings: Settings) -> tuple[WorkflowRun, Settings]:
    from ..agents.base import Blackboard
    from ..orchestrator import create_initial_execution
    from ..report.service import requires_corroboration
    from ..workflow import Step, Workflow

    preserved = deepcopy(content_state(detail))
    old = contract_from_scratch(preserved)
    if old is None or old.template not in WRITERS or detail.report is None:
        raise ValueError("没有可继续修订的任务快照")
    template = get_template(old.template)
    assert template is not None
    contract = build_contract(
        template,
        old.original_request,
        answers=old.confirmed_choices,
        tier=old.tier,
        strategy=old.strategy,
        quality=old.quality,
        attachments_csv=old.dataset_csv,
        dataset_source=old.dataset_source,
        demo_data=old.demo_data is True,
    )
    preserved[CONTRACT_SCRATCH_KEY] = contract.model_dump(mode="json")
    current = replace(
        settings, quality=old.quality, require_corroboration=requires_corroboration(detail)
    )
    if old.template == "paperRead":
        await _refresh_missing_abstracts(preserved, current)
        from .paper_abstract import checked_abstracts

        if not checked_abstracts(preserved):
            raise ValueError("没有可核验的完整摘要，且无法从已保存原件恢复；请补充原论文后再继续。")
    name = f"content_revision_{WRITERS[old.template]}"
    effective_query = effective_question(detail)
    execution = create_initial_execution(effective_query, name, current, requested_workflow=name)
    execution.definition = Workflow(
        name=name,
        description="复用原任务材料继续修订内容",
        steps=[Step(agent=WRITERS[old.template])],
    ).model_dump(mode="json")
    scratch = execution.checkpoint["scratch"]
    scratch.update(preserved)
    scratch[REVISION_KEY] = {
        "version": 1,
        "parent_run_id": detail.id,
        "source_version": source_version(detail),
        "template": old.template,
        "writer": WRITERS[old.template],
    }
    scratch[REVISION_SOURCES_KEY] = [source.model_dump(mode="json") for source in detail.sources]
    bb = Blackboard(
        query=effective_query,
        results=deepcopy(detail.results),
        report=detail.report.model_copy(deep=True),
        scratch=scratch,
    )
    execution.checkpoint = bb.model_dump(mode="json")
    return execution, current
