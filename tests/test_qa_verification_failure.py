"""A failed model check must not be presented as a lack of search evidence."""

import pytest

from deep_research.agents.base import RunContext
from deep_research.guardrails import SemanticEvidenceDecisionList
from deep_research.llm import ModelOutputTruncated
from deep_research.models import ExtractedFindingList
from deep_research.observability import Tracer
from deep_research.workbench.qa import answer_question
from tests.fakes import FakeLLM, FakeSearch


@pytest.mark.parametrize("stage", ["extraction", "verification"])
async def test_truncated_processing_preserves_the_real_failure_reason(settings, stage):
    class TruncatedVerifier(FakeLLM):
        verification_calls = 0

        async def parse(self, system, user, schema, **kwargs):
            if schema is ExtractedFindingList and stage == "extraction":
                raise ModelOutputTruncated(20000)
            if schema is SemanticEvidenceDecisionList:
                self.verification_calls += 1
                raise ModelOutputTruncated(20000)
            return await super().parse(system, user, schema, **kwargs)

    model = TruncatedVerifier()
    context = RunContext(
        llm=model, search_tool=FakeSearch(), tracer=Tracer(), settings=settings,
    )
    answer = await answer_question(
        "CASSI 的评价指标有哪些？", history=[], ctx=context, include_web=True,
    )
    assert answer.fallback and not answer.citations and not answer.findings
    assert "证据核验未能完成" in answer.answer
    assert "检索结果不足" not in answer.answer
    assert model.verification_calls == (1 if stage == "verification" else 0)
    assert model.stream_calls == 0
    assert any(t.get("status") == "incomplete" for t in answer.thoughts)


async def test_empty_extraction_is_still_an_evidence_shortfall(settings):
    class NoFindings(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):
            if schema is ExtractedFindingList:
                return ExtractedFindingList(findings=[])
            return await super().parse(system, user, schema, **kwargs)

    model = NoFindings()
    context = RunContext(
        llm=model, search_tool=FakeSearch(), tracer=Tracer(), settings=settings,
    )
    answer = await answer_question(
        "CASSI 的评价指标有哪些？", history=[], ctx=context, include_web=True,
    )
    assert answer.fallback and "检索结果不足" in answer.answer
    assert "核验未能完成" not in answer.answer
    assert model.stream_calls == 0
