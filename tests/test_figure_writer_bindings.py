from __future__ import annotations

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.config import Settings
from deep_research.models import Report, ResearchResult
from deep_research.observability import Tracer
from deep_research.persistence.repository import LeaseLostError
from deep_research.workbench.content_revision import REVISION_KEY
from deep_research.workbench.figure_review import review_figure
from deep_research.workbench.prose_review import ProseReviewer
from deep_research.workbench.support import SupportReviewer, evidence_records
from deep_research.workbench.templates import AUTO_RESEARCH
from deep_research.workbench.writers import TemplateWriter
from tests.fakes import FakeLLM, FakeSearch, verified_finding
from tests.test_figure_bindings import Judge, diagram


async def test_generator_receives_report_scope_and_cannot_downgrade_bindings():
    seen = []

    class Model(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):
            seen.append(str(user))
            return diagram().model_copy(update={"evidence_mode": "legacy"})

    ctx = RunContext(llm=Model(), search_tool=FakeSearch(), tracer=Tracer(), settings=Settings())
    writer = TemplateWriter()
    writer.name = "research_writer"
    result = await writer.concept_figure(
        ctx,
        AUTO_RESEARCH,
        "CITED-MATERIAL",
        query="QUESTION-SCOPE",
        markdown="## 分析\nREPORT-CORE\n\n## 参考来源\n[1] BIBLIOGRAPHY-NOT-NEEDED",
    )
    assert result["evidence_mode"] == "scoped"
    assert "QUESTION-SCOPE" in seen[0] and "REPORT-CORE" in seen[0]
    assert "BIBLIOGRAPHY-NOT-NEEDED" not in seen[0]


async def test_figure_generation_propagates_lost_lease():
    class Model(FakeLLM):
        async def parse(self, *args, **kwargs):
            raise LeaseLostError("taken over")

    ctx = RunContext(llm=Model(), search_tool=FakeSearch(), tracer=Tracer(), settings=Settings())
    writer = TemplateWriter()
    writer.name = "research_writer"
    with pytest.raises(LeaseLostError):
        await writer.concept_figure(ctx, AUTO_RESEARCH, "material")


async def test_revision_reuses_a_bound_diagram_without_generating_or_rejudging_it(monkeypatch):
    results = [ResearchResult(sub_question="q", findings=[verified_finding("结论X")])]
    graph, llm = diagram(), Judge()
    record = await review_figure(
        graph, SupportReviewer(llm, evidence_records(results, {"https://a.com": 1}), 50000)
    )
    llm.requests.clear()

    async def approved(self, body):
        return {"status": "pass", "issues": []}

    monkeypatch.setattr(ProseReviewer, "review", approved)

    class Writer(TemplateWriter):
        name = "research_writer"

        async def _write_checked(self, *args, **kwargs):
            return "## 结论\n结论X [1]。", None

        async def concept_figure(self, *args, **kwargs):
            raise AssertionError("Existing accepted diagram should be reused")

    bb = Blackboard(
        query="q",
        results=results,
        report=Report(query="q", markdown="", citations=["https://a.com"]),
        scratch={
            REVISION_KEY: {"parent_run_id": "parent"},
            "workbench": {
                "template": "autoResearch",
                "extras": {
                    "concept_figure": graph.model_dump(mode="json"),
                    "figure_review": record,
                },
            },
        },
    )
    await Writer().step(
        bb, RunContext(llm=llm, search_tool=FakeSearch(), tracer=Tracer(), settings=Settings())
    )
    assert not llm.requests
    assert bb.scratch["workbench"]["extras"]["figure_review"]["status"] == "pass"
