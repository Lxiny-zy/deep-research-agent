from __future__ import annotations

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.agents.researcher import Researcher
from deep_research.models import (
    ExtractedFindingList,
    ResearchPlan,
    ResearchResult,
    Source,
    SubQuestion,
)
from deep_research.observability import Tracer
from deep_research.orchestrator import DeepResearchAgent
from deep_research.persistence.repository import LeaseLostError
from deep_research.workflow import _unseen_sub_questions
from tests.fakes import FakeLLM, FakeSearch, verified_finding

SOURCES = [
    Source(title="A", url="https://a.com", content="Alpha uses an established spatial method."),
    Source(
        title="B", url="https://b.com", content="Beta uses a spectral method under known masks."
    ),
]


class Reader(FakeLLM):
    def __init__(self):
        super().__init__()
        self.extraction_prompts = []

    async def parse(self, system, user, schema, **kwargs):
        if schema is ExtractedFindingList:
            self.extraction_prompts.append(user)
            return ExtractedFindingList(
                findings=[
                    verified_finding(s.content, s.url, s.content) for s in SOURCES if s.url in user
                ]
            )
        return await super().parse(system, user, schema, **kwargs)


class Queries(FakeSearch):
    def __init__(self):
        self.queries = []

    async def search(self, query, *, max_results=5):
        self.queries.append(query)
        if query == "failed":
            raise TimeoutError("private provider error")
        if query == "alpha":
            return SOURCES[:1]
        if query == "beta":
            return [*SOURCES, SOURCES[0].model_copy(update={"content": "Different later snapshot"})]
        return []


def test_plan_queries_are_distinct_from_full_questions_and_roundtrip():
    question = "比较完整研究问题，保留地区、年份和实验条件"
    sub = SubQuestion(question=question, search_queries=["alpha", " beta ", "alpha", " "])
    assert sub.question == question and sub.search_queries == ["alpha", "beta"]
    plan = ResearchPlan(interpretation="完整范围", sub_questions=[sub])
    restored = ResearchPlan.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert SubQuestion(question=question).search_queries == []


async def test_compact_queries_retrieve_but_full_question_and_context_drive_extraction(settings):
    llm, search = Reader(), Queries()
    researcher = Researcher(llm, search, Tracer(), settings)
    question = "比较 Alpha 与 Beta 在已知掩膜和给定实验条件下的方法与局限"
    result = await researcher.run(
        question,
        [verified_finding("前驱已确认的条件")],
        search_queries=["alpha", "failed", "beta", "alpha"],
    )
    assert search.queries == ["alpha", "failed", "beta"]
    assert result.sub_question == question and len(result.findings) == 2
    assert len(llm.extraction_prompts) == 1
    prompt = llm.extraction_prompts[0]
    assert question in prompt and "前驱已确认的条件" in prompt
    assert all(s.content in prompt for s in SOURCES)
    assert "Different later snapshot" not in prompt
    assert "private provider error" not in str(researcher.tracer.events)


async def test_researcher_restores_pending_queries_from_a_json_checkpoint(settings):
    llm, search = Reader(), Queries()
    sub = SubQuestion(question="完整问题", search_queries=["alpha", "beta"])
    checkpoint = Blackboard(query="原始任务", scratch={"pending_sub_questions": [sub]}).model_dump(
        mode="json"
    )
    bb = Blackboard.model_validate(checkpoint)
    await Researcher().step(
        bb, RunContext(llm=llm, search_tool=search, tracer=Tracer(), settings=settings)
    )
    assert search.queries == ["alpha", "beta"]
    assert bb.results[0].sub_question == "完整问题"


async def test_legacy_dag_passes_queries_without_changing_predecessor_context(settings):
    class Probe:
        def __init__(self):
            self.calls = []

        async def run(self, question, context_findings=None, *, search_queries=None):
            self.calls.append((question, search_queries, context_findings))
            return ResearchResult(sub_question=question, findings=[verified_finding(question)])

    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch())
    probe = Probe()
    agent.researcher = probe
    try:
        await agent._research_dag(
            [
                SubQuestion(question="A", search_queries=["alpha"]),
                SubQuestion(question="B", search_queries=["beta"], depends_on=[0]),
            ]
        )
        assert [(q, queries) for q, queries, _ in probe.calls] == [
            ("A", ["alpha"]),
            ("B", ["beta"]),
        ]
        assert probe.calls[1][2][0].statement == "A"
    finally:
        await agent.aclose()


def test_reflection_queries_are_kept_with_only_the_unseen_sub_questions():
    bb = Blackboard(
        query="主题",
        plan=ResearchPlan(interpretation="范围", sub_questions=[SubQuestion(question="A")]),
    )
    fresh = _unseen_sub_questions(bb, ["A", "B", "B"], {"A": ["old"], "B": [" beta ", "beta"]})
    assert len(fresh) == 1 and fresh[0].question == "B" and fresh[0].search_queries == ["beta"]


async def test_legacy_question_falls_back_without_an_extra_planner_call(settings):
    llm, search = Reader(), Queries()
    result = await Researcher(llm, search, Tracer(), settings).run("alpha")
    assert search.queries == ["alpha"] and result.findings


async def test_lost_lease_stops_other_search_queries(settings):
    class Lost(Queries):
        async def search(self, query, **kwargs):
            self.queries.append(query)
            raise LeaseLostError("owned task was reassigned")

    search = Lost()
    with pytest.raises(LeaseLostError):
        await Researcher(Reader(), search, Tracer(), settings).run(
            "完整问题", search_queries=["alpha", "beta"]
        )
    assert search.queries == ["alpha"]
