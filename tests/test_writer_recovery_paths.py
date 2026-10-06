"""Recover failed local editing and reuse reviewer drafts without weakening checks."""

import asyncio
from dataclasses import replace

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.models import Report, ResearchResult, Source
from deep_research.observability import Tracer
from deep_research.persistence.repository import LeaseLostError
from deep_research.workbench.quality import QualityPolicy
from deep_research.workbench.templates import get_template
from deep_research.workbench.writers import PeerReviewer, ResearchWriter
from deep_research.workbench.writing_progress import WritingProgressError
from tests.fakes import FakeLLM, FakeSearch, verified_finding


class Model(FakeLLM):
    def __init__(self, *bodies):
        super().__init__()
        self.bodies = list(bodies)
        self.prompts = []

    async def stream(self, system, user, **kwargs):
        self.prompts.append(user)
        yield self.bodies[len(self.prompts) - 1]


class Auditor:
    def __init__(self, require_score=False):
        self.checked = []
        self.primed = []
        self.require_score = require_score

    def prime(self, body, record):
        self.primed.append(body)
        return True

    async def review(self, body):
        self.checked.append(body)
        return {
            "issues": ["unsupported fact"] if "BAD" in body else [],
            "can_revise": True,
            "requirements_review": {
                "status": "fail" if self.require_score and "评分：7/10" not in body else "pass",
                "issues": ["缺少评分"] if self.require_score and "评分：7/10" not in body else [],
                "can_revise": True,
            },
        }


def inputs(settings, model, key="autoResearch", *, seed=None):
    bb = Blackboard(
        query="q", results=[ResearchResult(sub_question="q", findings=[verified_finding()])]
    )
    if seed is not None:
        bb.report = Report(query="q", markdown=seed, citations=["https://a.com"])
        bb.scratch["content_revision"] = {}
        bb.scratch["prose_review"] = {}
    ctx = RunContext(llm=model, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    template = replace(get_template(key), sections=(), min_length=0)
    policy = QualityPolicy(max_revisions=1, register_check=False, require_limitations=False)
    return bb, ctx, template, policy


async def checked(writer, bb, ctx, template, policy, auditor):
    return await writer._write_checked(
        bb,
        ctx,
        template,
        None,
        "material",
        {"https://a.com": 1},
        policy=policy,
        min_citations=1,
        require_corroboration=False,
        reviewer=auditor,
    )


async def test_local_edit_error_falls_back_to_full_write_and_rechecks(settings, monkeypatch):
    from deep_research.workbench import prose_edit

    model = Model("BAD [1].", "发现X [1].")
    bb, ctx, template, policy = inputs(settings, model)
    auditor = Auditor()

    async def broken(*args, **kwargs):
        raise ValueError("invalid local edit")

    monkeypatch.setattr(prose_edit, "repair_paragraphs", broken)
    body, log = await checked(ResearchWriter(), bb, ctx, template, policy, auditor)
    assert body == "发现X [1]." and not log.remaining
    assert len(model.prompts) == 2 and auditor.checked == ["BAD [1].", "发现X [1]."]
    assert any(
        event.data and event.data.get("category") == "local_revision_fallback"
        for event in ctx.tracer.events
    )


@pytest.mark.parametrize(
    "error", [LeaseLostError("lost"), asyncio.CancelledError(), WritingProgressError("storage")]
)
async def test_lost_execution_authority_never_starts_full_rewrite(settings, monkeypatch, error):
    from deep_research.workbench import prose_edit

    model = Model("BAD [1].")
    bb, ctx, template, policy = inputs(settings, model)

    async def stopped(*args, **kwargs):
        raise error

    monkeypatch.setattr(prose_edit, "repair_paragraphs", stopped)
    with pytest.raises(type(error)):
        await checked(ResearchWriter(), bb, ctx, template, policy, Auditor())
    assert len(model.prompts) == 1


async def test_exhausted_budget_does_not_trigger_another_generation(settings, monkeypatch):
    from deep_research.token_budget import TokenBudgetExceeded
    from deep_research.workbench import prose_edit

    model = Model("BAD [1].")
    bb, ctx, template, policy = inputs(settings, model)

    async def exhausted(*args, **kwargs):
        raise TokenBudgetExceeded("budget")

    monkeypatch.setattr(prose_edit, "repair_paragraphs", exhausted)
    body, _ = await checked(ResearchWriter(), bb, ctx, template, policy, Auditor())
    assert body == "BAD [1]." and len(model.prompts) == 1


async def test_peer_review_revision_reuses_existing_draft_and_score(settings):
    seed = "发现X [1].\n\n## 审稿结论\n\n评分：7/10"
    model = Model()
    bb, ctx, template, policy = inputs(settings, model, "peerReview", seed=seed)
    auditor = Auditor(require_score=True)
    body, log = await checked(PeerReviewer(), bb, ctx, template, policy, auditor)
    assert not model.prompts and auditor.primed
    assert "发现X [1]." in body and body.count("评分：7/10") == 1
    assert bb.scratch["_review_score"] == 7 and not log.remaining


async def test_score_is_visible_to_coverage_but_not_treated_as_a_source_measurement(settings):
    model = Model("发现X [1].\n\n评分：7/10")
    bb, ctx, template, policy = inputs(settings, model, "peerReview")
    body, log = await checked(
        PeerReviewer(),
        bb,
        ctx,
        template,
        policy.model_copy(update={"max_revisions": 0}),
        Auditor(require_score=True),
    )
    assert "评分：7/10" in body and not log.remaining


async def test_critical_score_conflict_converges_through_local_rating_edit(settings, monkeypatch):
    from deep_research.workbench import prose_edit
    from deep_research.workbench.peer_review_items import PeerReviewChecker
    from tests.test_peer_review_items import EVIDENCE, Judge, material

    draft = "## 不足\n\n发现X [1]。\n\n评分：7/10"
    model = Model(draft)
    bb, ctx, template, policy = inputs(settings, model, "peerReview")
    checker = PeerReviewChecker(Judge(critical=True), EVIDENCE, 50000, query="q")

    class CheckedAuditor(Auditor):
        async def review(self, body):
            peer = await checker.review(body, *material(body))
            return {"issues": peer["issues"], "can_revise": peer["can_revise"],
                    "peer_review": peer}

    edits = []

    async def edit(llm, reviewer, body, audit, *, local_problems):
        assert local_problems and all("评分" in issue for _, issue in local_problems)
        edits.append(body)
        return body.replace("评分：7/10", "评分：5/10")

    monkeypatch.setattr(prose_edit, "repair_paragraphs", edit)
    body, log = await checked(PeerReviewer(), bb, ctx, template, policy, CheckedAuditor())
    assert "评分：5/10" in body and not log.remaining
    assert len(model.prompts) == len(edits) == 1
    assert checker.llm.classifications == 1


def test_degraded_review_does_not_append_a_rating():
    bb = Blackboard(
        query="q", scratch={"_review_score": 7, "_report_validation": {"fallback": True}}
    )
    report = Report(query="q", markdown="仅保留核验素材用于诊断。")
    extras = PeerReviewer().postprocess(bb, report, get_template("peerReview"))
    assert "评分" not in report.markdown and extras["score"] is None


async def test_style_advice_alone_does_not_start_revision(settings):
    model = Model("其实，发现X [1]。")
    bb, ctx, template, policy = inputs(settings, model)
    body, log = await checked(
        ResearchWriter(),
        bb,
        ctx,
        template,
        policy.model_copy(update={"register_check": True}),
        Auditor(),
    )
    assert body == "其实，发现X [1]。" and len(model.prompts) == 1
    assert not log.remaining and any("口语化" in issue for issue in log.advisories)


async def test_statistical_style_advice_does_not_rewrite_verified_results(settings):
    from deep_research.workbench.analysis import DataAnalyst, analyse, fallback_report
    from deep_research.workbench.contract import build_contract

    settings.quality = {"max_revisions": 2, "register_check": True, "require_limitations": False}
    query = "描述数据\nmethod,value\nA,1\nA,2\nB,3\nB,4\n"
    contract = build_contract(get_template("dataAnalysis"), query, quality=settings.quality)
    body = "其实，以下是统计结果。\n\n" + fallback_report(
        analyse(contract.dataset_csv, contract.focus)
    )
    model = Model(body, body, body)
    bb = Blackboard(query=query, scratch={"task_contract": contract.model_dump(mode="json")})
    ctx = RunContext(llm=model, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    result = await DataAnalyst().step(bb, ctx)
    assert len(model.prompts) == 1
    assert result.scratch["workbench"]["extras"]["revision"]["advisories"]


async def test_peer_reviewer_step_reuses_a_bound_parent_report(settings, tmp_path, monkeypatch):
    import hashlib
    import json
    from datetime import UTC, datetime

    from deep_research.orchestrator import create_initial_execution
    from deep_research.persistence.repository import RunDetail
    from deep_research.workbench.delivery import pdf
    from deep_research.workbench.delivery_store import build_or_load, load_version, retry_format
    from deep_research.workbench.peer_review_items import PeerReviewChecker
    from deep_research.workbench.publish import build_bundle
    from tests.test_workbench import WorkbenchLLM

    settings.quality = {
        "max_revisions": 0,
        "register_check": False,
        "require_limitations": False,
        "forbid_abstract_citations": False,
    }
    template = get_template("peerReview")
    draft = "\n\n".join(
        f"## {section.title}\n"
        + (
            "建议围绕发现X进行复现实验，并报告复现前后的指标差异 [1]。"
            if section.title in {"不足", "详细意见"}
            else "发现X [1]。"
        )
        for section in template.sections
    )
    draft += "\n\n评分：7/10"
    source = Source(
        url="https://a.com", content="内容A提供了可核验的原文证据", section_title="Method",
    )
    finding = verified_finding()
    finding.verification.source_content_hash = hashlib.sha256(source.content.encode()).hexdigest()
    initial = Blackboard(
        query="评审这份材料",
        results=[ResearchResult(sub_question="q", findings=[finding])],
        scratch={"paper_sources": [source.model_dump(mode="json")]},
    )
    class ActionableWorkbench(WorkbenchLLM):
        async def parse(self, system, user, schema, **kwargs):
            response = await super().parse(system, user, schema, **kwargs)
            if schema.__name__ == "PeerReviewClassifications":
                from deep_research.workbench.peer_review_items import PeerReviewAction

                units = {unit["id"]: unit for unit in json.loads(user)["items"]}
                for item in response.items:
                    if "建议围绕发现X进行复现实验" in units[item.unit_id]["text"]:
                        item.kind, item.severity = "suggestion", None
                        item.action = PeerReviewAction(
                            target_quote="发现X", action_quote="进行复现实验",
                            completion_quote="报告复现前后的指标差异",
                        )
            return response

    first_ctx = RunContext(
        llm=ActionableWorkbench(draft),
        search_tool=FakeSearch(),
        tracer=Tracer(),
        settings=settings,
        evidence_sources=[source],
    )
    parent = await PeerReviewer().step(initial, first_ctx)
    remaining = parent.scratch["workbench"]["extras"]["revision"]["remaining"]
    assert parent.scratch["prose_review"]["status"] == "pass", remaining

    # Persist the real writer output and retry only a failed renderer. Review is frozen.
    execution = create_initial_execution(parent.query, template.workflow, settings)
    execution.checkpoint["scratch"] = parent.scratch.copy()
    detail = RunDetail(
        id="peer-frozen", query=parent.query, status="done", created_at=datetime.now(UTC),
        report=parent.report, results=parent.results, sources=[source], orchestration=execution,
    )

    async def no_model(*args, **kwargs):
        raise AssertionError("formatting must reuse the frozen review")

    def failed_pdf(*args, **kwargs):
        raise pdf.PdfRenderError("temporary failure")

    with monkeypatch.context() as patch:
        patch.setattr(PeerReviewChecker, "review", no_model)
        with monkeypatch.context() as render_patch:
            render_patch.setattr(pdf, "render_pdf", failed_pdf)
            bundle = build_or_load(detail, str(tmp_path), None, build_bundle)
        assert next(g for g in bundle.gates if g.name == "review").status == "pass"
        assert not bundle.render_context["blocked"]
        structured = next(f for f in bundle.files if f.name.endswith("-review-items.json"))
        data = json.loads(structured.data)
        assert data["review"] == parent.scratch["prose_review"]["peer_review"]
        assert data["evidence"] and data["delivery_blocked"] is False
        retried = retry_format(
            detail, str(tmp_path), None, bundle.content_version, "pdf", "peer-pdf-retry",
        )
        assert next(f.data for f in retried.files if f.name == structured.name) == structured.data
        original = load_version(detail, str(tmp_path), bundle.content_version)
        assert original.registry() == bundle.registry()

    child = parent.model_copy(deep=True)
    child.scratch["content_revision"] = {"parent_run_id": "parent"}

    class ReuseOnly(WorkbenchLLM):
        async def stream(self, *args, **kwargs):
            raise AssertionError("the reviewed parent draft must not be regenerated")
            yield ""

    next_ctx = RunContext(
        llm=ReuseOnly(""),
        search_tool=FakeSearch(),
        tracer=Tracer(),
        settings=settings,
        evidence_sources=first_ctx.evidence_sources,
    )
    revised = await PeerReviewer().step(child, next_ctx)
    assert revised.report.markdown == parent.report.markdown
    assert revised.report.markdown.count("评分：7/10") == 1
    assert revised.scratch["workbench"]["extras"]["score"] == 7
