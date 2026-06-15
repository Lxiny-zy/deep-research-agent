"""验证流式综合：run_stream 产出正文增量，run() 仍返回完整 Report，token 透传 SSE。"""

from __future__ import annotations

import pytest

from deep_research.agents.synthesizer import Synthesizer
from deep_research.models import Finding, Report, ResearchResult
from deep_research.observability import Tracer
from deep_research.orchestrator import DeepResearchAgent
from tests.fakes import FakeLLM, FakeSearch


def _results() -> list[ResearchResult]:
    return [
        ResearchResult(
            sub_question="Q",
            findings=[Finding(statement="s", source_url="https://a.com")],
        )
    ]


@pytest.mark.asyncio
async def test_run_stream_yields_body_deltas(settings):
    synth = Synthesizer(FakeLLM(), Tracer(), settings)
    deltas = [d async for d in synth.run_stream("Q", _results())]
    assert deltas  # 有增量产出
    assert "".join(deltas).strip()  # 拼起来是非空正文


@pytest.mark.asyncio
async def test_run_returns_complete_report(settings):
    synth = Synthesizer(FakeLLM(), Tracer(), settings)
    report = await synth.run("Q", _results())
    assert isinstance(report, Report)
    assert "## 参考来源" in report.markdown
    assert report.citations == ["https://a.com"]


@pytest.mark.asyncio
async def test_orchestrator_stream_includes_token_events(settings):
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch())
    events = [ev async for ev in agent.run_stream("测试问题")]
    types = [e.type for e in events]
    assert "token" in types  # 流式 token 透传到 SSE 事件流
    assert types[-1] in ("done", "error")
    assert any(e.type == "report" for e in events)
