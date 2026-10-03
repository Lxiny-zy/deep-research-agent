from __future__ import annotations

import pytest

from deep_research.workbench.figure_request import concept_figure_enabled


@pytest.mark.parametrize(
    "query",
    [
        "总结材料，不需要额外图示。",
        "无需配图，只交付报告。",
        "请不要生成概念图。",
        "本次不添加图示。",
        "Summarize the findings. No additional diagrams please.",
        "Do not include figures.",
        "Don't generate companion diagrams.",
        "添加图示。改为只写正文，不需要配图。",
    ],
)
def test_explicit_opt_out_disables_optional_companion_figures(query):
    assert not concept_figure_enabled(query)


@pytest.mark.parametrize(
    "query",
    [
        "总结材料并提供图示。",
        "不要遗漏图示中的关键关系。",
        "不需要额外实验，需要图示。",
        "不需要配图。现在补充一张示意图。",
        "No diagrams. Add a conceptual figure.",
        "Don't omit diagrams.",
        "论文中的“不需要图示”是一句引文。",
    ],
)
def test_other_negations_quotes_and_later_requests_keep_figures_enabled(query):
    assert concept_figure_enabled(query)


async def test_revision_does_not_reuse_an_old_optional_figure_after_opt_out(settings, monkeypatch):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.models import ResearchResult
    from deep_research.observability import Tracer
    from deep_research.workbench import figure_edit
    from deep_research.workbench.content_revision import REVISION_KEY
    from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
    from deep_research.workbench.templates import LIT_REVIEW
    from deep_research.workbench.writers import SurveyWriter
    from tests.fakes import FakeSearch, verified_finding
    from tests.test_workbench import WorkbenchLLM

    query = "总结已提供的结论，不需要额外图示。"
    contract = build_contract(
        LIT_REVIEW,
        query,
        strategy="quick",
        quality={
            "max_revisions": 0,
            "register_check": False,
        },
    )
    contract.min_citations = 1
    body = "\n\n".join(
        f"## {s.title}\n" + ("本文讨论给定材料。" if s.key == "abstract" else "发现X [1]。" * 60)
        for s in LIT_REVIEW.sections
    )
    old_figure = {
        "title": "旧图",
        "evidence_mode": "scoped",
        "nodes": [{"id": "a", "label": "发现X", "citations": [1]}, {"id": "b", "label": "主题"}],
    }
    bb = Blackboard(
        query=query,
        results=[ResearchResult(sub_question="q", findings=[verified_finding()])],
        scratch={
            CONTRACT_SCRATCH_KEY: contract.model_dump(mode="json"),
            REVISION_KEY: {},
            "workbench": {"extras": {"concept_figure": old_figure}},
        },
    )

    def unexpected(*args, **kwargs):
        raise AssertionError("The old figure must not be reused after an explicit opt-out")

    monkeypatch.setattr(figure_edit, "prime_figure", unexpected)

    class Writer(SurveyWriter):
        async def concept_figure(self, *args, **kwargs):
            unexpected()

    ctx = RunContext(
        llm=WorkbenchLLM(body), search_tool=FakeSearch(), tracer=Tracer(), settings=settings
    )
    await Writer().step(bb, ctx)
    extras = bb.scratch["workbench"]["extras"]
    assert extras["prose_review"]["status"] == "pass"
    assert not extras.get("revision", {}).get("remaining"), extras.get("revision")
    assert extras["concept_figure_skipped"] == "按本次任务要求不生成配套图示"
    assert "concept_figure" not in extras


async def test_writer_does_not_call_figure_model_for_explicit_opt_out(settings):
    from deep_research.agents.base import RunContext
    from deep_research.observability import Tracer
    from deep_research.workbench.templates import AUTO_RESEARCH
    from deep_research.workbench.writers import SurveyWriter
    from tests.fakes import FakeLLM, FakeSearch

    class Never(FakeLLM):
        async def parse(self, *args, **kwargs):
            raise AssertionError("Optional figure generation was explicitly disabled")

    ctx = RunContext(llm=Never(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    assert (
        await SurveyWriter().concept_figure(
            ctx, AUTO_RESEARCH, "已核验材料 [1]", query="总结结论，不需要额外图示。"
        )
        is None
    )
