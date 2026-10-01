from __future__ import annotations

import pytest

from deep_research.agents.base import RunContext
from deep_research.models import FindingList
from deep_research.observability import Tracer
from deep_research.workbench.qa import answer_question
from deep_research.workbench.qa_cache import PaperEvidenceCache
from tests.fakes import FakeLLM, FakeSearch


class CountingLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self.extractions = 0
        self.fail_once = False

    async def parse(self, system, user, schema, **kwargs):  # type: ignore[no-untyped-def]
        if schema is FindingList:
            self.extractions += 1
            assert "采用已有算法不能自动当作原创贡献" in system
            if self.fail_once:
                self.fail_once = False
                raise ValueError("temporary extraction failure")
        return await super().parse(system, user, schema, **kwargs)

    async def stream(self, system, user, **kwargs):  # type: ignore[no-untyped-def]
        yield "有证据支持的结论 [1]。"


async def test_paper_evidence_cache_reuses_only_identical_scoped_inputs(settings) -> None:
    llm = CountingLLM()
    cache = PaperEvidenceCache()
    ctx = RunContext(llm=llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    sources = await FakeSearch().search("paper")

    async def ask(scope="alice/run", history=None):  # type: ignore[no-untyped-def]
        return await answer_question(
            "核心贡献是什么？",
            history=history or [],
            ctx=ctx,
            paper_sources=sources,
            paper_cache=cache,
            cache_scope=scope,
        )

    first = await ask()
    second = await ask(history=[{"query": "核心贡献是什么？", "answer": first.answer}])
    assert llm.extractions == 1
    assert any("复用已核验" in item["observation"] for item in second.thoughts)
    await ask("bob/run")
    assert llm.extractions == 2
    sources[0] = sources[0].model_copy(update={"content": sources[0].content + "新版本"})
    await ask()
    assert llm.extractions == 3


async def test_failed_extraction_is_not_cached(settings) -> None:
    llm = CountingLLM()
    llm.fail_once = True
    cache = PaperEvidenceCache()
    ctx = RunContext(llm=llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    sources = await FakeSearch().search("paper")
    with pytest.raises(ValueError, match="temporary extraction failure"):
        await answer_question(
            "核心贡献是什么？",
            history=[],
            ctx=ctx,
            paper_sources=sources,
            paper_cache=cache,
            cache_scope="alice/run",
        )
    result = await answer_question(
        "核心贡献是什么？",
        history=[],
        ctx=ctx,
        paper_sources=sources,
        paper_cache=cache,
        cache_scope="alice/run",
    )
    assert result.findings and not result.fallback
    assert llm.extractions == 2


async def test_paper_verifier_failure_is_not_misreported_as_missing_evidence(settings):
    from deep_research.guardrails import SemanticEvidenceDecisionList
    from deep_research.llm import ModelOutputTruncated

    class VerifierFailure(CountingLLM):
        async def parse(self, system, user, schema, **kwargs):
            if schema is SemanticEvidenceDecisionList:
                raise ModelOutputTruncated(8192)
            return await super().parse(system, user, schema, **kwargs)

    ctx = RunContext(
        llm=VerifierFailure(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings
    )
    with pytest.raises(ModelOutputTruncated):
        await answer_question(
            "论文创新是什么？",
            history=[],
            ctx=ctx,
            paper_sources=await FakeSearch().search("paper"),
        )


async def test_answer_repairs_missing_citations_before_falling_back(settings) -> None:
    class RepairLLM(CountingLLM):
        drafts = 0

        async def stream(self, system, user, **kwargs):  # type: ignore[no-untyped-def]
            self.drafts += 1
            yield "发现X，没有引用角标的陈述。" if self.drafts == 1 else "发现X [1]。"

    llm = RepairLLM()
    ctx = RunContext(llm=llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    events = []
    result = await answer_question(
        "核心贡献是什么？",
        history=[],
        ctx=ctx,
        paper_sources=await FakeSearch().search("paper"),
        on_event=events.append,
    )
    assert llm.drafts == 2
    assert not result.fallback and result.answer == "发现X [1]。"
    assert any(event["type"] == "reset" for event in events)
