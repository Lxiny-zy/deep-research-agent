from __future__ import annotations

import pytest

from deep_research.models import Report, Source
from deep_research.orchestrator import DeepResearchAgent
from tests.fakes import FakeLLM, FakeSearch


@pytest.mark.asyncio
async def test_run_produces_grounded_report(settings):
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch())
    report = await agent.run("测试问题")

    assert isinstance(report, Report)
    assert report.markdown
    # 引用溯源：报告来源必须来自真实检索结果
    assert "https://a.com" in report.citations
    assert "## 参考来源" in report.markdown


@pytest.mark.asyncio
async def test_events_emitted(settings):
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch())
    await agent.run("测试问题")
    types = {e.type for e in agent.tracer.events}
    assert "report" in types and "done" in types
    stages = {e.stage for e in agent.tracer.events}
    assert {"PLANNER", "RESEARCHER", "REFLECTOR", "SYNTHESIZER"} <= stages


@pytest.mark.asyncio
async def test_run_stream_terminates(settings):
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch())
    events = [ev async for ev in agent.run_stream("测试问题")]
    assert events[-1].type in ("done", "error")
    assert any(e.type == "report" for e in events)


class _FlakyOnceSearch(FakeSearch):
    """第一次检索抛异常，之后正常——模拟单个子问题的瞬时检索失败。"""

    def __init__(self) -> None:
        self.calls = 0

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("检索后端瞬时故障")
        return await super().search(query, max_results=max_results)


@pytest.mark.asyncio
async def test_run_stream_survives_researcher_error(settings):
    """RESEARCHER 的 error 是被隔离的单点失败：流不得提前断，最终仍产出报告。"""
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=_FlakyOnceSearch())
    events = [ev async for ev in agent.run_stream("测试问题")]
    # 流中确实出现了 RESEARCHER error 事件……
    assert any(e.stage == "RESEARCHER" and e.type == "error" for e in events)
    # ……但流走到了 ORCHESTRATOR 终态，且是 done 而非 error
    assert events[-1].stage == "ORCHESTRATOR"
    assert events[-1].type == "done"
    assert any(e.type == "report" for e in events)
