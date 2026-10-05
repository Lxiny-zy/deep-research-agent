"""History must fit the smallest participating role, not just the answer writer."""

from deep_research.agents.base import RunContext
from deep_research.agents.researcher import Researcher
from deep_research.models import ResearchResult
from deep_research.observability import Tracer
from deep_research.workbench.qa import answer_question
from tests.fakes import FakeLLM, FakeSearch


async def test_retrieval_history_respects_a_smaller_role_window(settings, monkeypatch):
    writer = FakeLLM()
    researcher = FakeLLM()
    researcher.input_capacity_chars = 4000
    captured = []

    async def run(self, query, **kwargs):
        captured.append(query)
        return ResearchResult(sub_question=query)

    monkeypatch.setattr(Researcher, "run", run)
    ctx = RunContext(
        llm=writer, search_tool=FakeSearch(), tracer=Tracer(), settings=settings,
        llm_resolver=lambda role: researcher if role == "researcher" else writer,
    )
    history = [{"query": "CASSI 评价指标", "answer": "历史说明。" * 300}]
    history += [
        {"query": f"第 {index} 个方法如何对比？" * 20, "answer": "背景回答。" * 100}
        for index in range(10)
    ]
    await answer_question("2026年有新指标吗？", history=history, ctx=ctx, include_web=True)
    assert len(captured) == 1
    assert len(captured[0]) <= 1000  # one quarter of the actual researcher's input hint
    assert "CASSI" in captured[0] and "2026年有新指标吗？" in captured[0]
    assert "本轮问题" in captured[0]
