"""验证 orchestrator 注入 repo 后落库完整：计划/结果/报告/事件齐全，token 事件不落库。"""

from __future__ import annotations

import pytest

from deep_research.orchestrator import DeepResearchAgent
from deep_research.persistence.memory_repository import InMemoryRepository
from tests.fakes import FakeLLM, FakeSearch


@pytest.mark.asyncio
async def test_run_persists_full_trace(settings):
    repo = InMemoryRepository()
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch(), repo=repo)
    report = await agent.run("测试问题")

    runs = await repo.list_runs()
    assert len(runs) == 1
    run_id = runs[0].id

    detail = await repo.get_run(run_id)
    assert detail is not None
    assert detail.status == "done"
    assert detail.report is not None
    assert detail.report.markdown == report.markdown
    assert detail.sub_questions  # 计划已落库
    assert detail.results  # 结果已落库

    events = await repo.get_events(run_id)
    assert events
    assert {"start", "report", "done"} <= {e.type for e in events}
    # token 事件是瞬态的，不落库
    assert all(e.type != "token" for e in events)


@pytest.mark.asyncio
async def test_run_without_repo_unaffected(settings):
    # repo=None 时行为与无持久化一致：正常返回完整报告
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch())
    report = await agent.run("测试问题")
    assert report.markdown
    assert "## 参考来源" in report.markdown
