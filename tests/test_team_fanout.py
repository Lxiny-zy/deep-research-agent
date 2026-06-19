"""L4 多团队并行（teams 流程）：planner 切分 → 各团队隔离并行研究 → aggregator 归并。

覆盖：正常 fan-out + 归并产出报告；单个团队检索故障被隔离、其余团队仍贡献结果。
"""

from __future__ import annotations

import pytest

from deep_research.models import Report, Source
from deep_research.orchestrator import DeepResearchAgent
from tests.fakes import FakeLLM, FakeSearch


@pytest.mark.asyncio
async def test_teams_workflow_fans_out_and_aggregates(settings) -> None:
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch(), workflow="teams")
    report = await agent.run("测试问题")

    assert isinstance(report, Report)
    stages = {e.stage for e in agent.tracer.events}
    assert "AGGREGATOR" in stages  # 走到归并角色
    # 分派事件透出团队列表（planner 切出 2 个子问题 → 2 个团队）
    fan = [e for e in agent.tracer.events if e.data and "teams" in e.data]
    assert fan and len(fan[0].data["teams"]) == 2
    # 两个团队各自检索 → 合并后报告有引用
    assert report.citations
    assert len(agent.tracer.events) > 0


class _FailOneFocusSearch(FakeSearch):
    """对某个 focus 的检索抛错，模拟单个子团队检索故障。"""

    def __init__(self, bad_focus: str) -> None:
        self.bad = bad_focus

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        if self.bad in query:
            raise RuntimeError("该团队检索故障")
        return await super().search(query, max_results=max_results)


@pytest.mark.asyncio
async def test_team_failure_is_isolated(settings) -> None:
    """一个团队检索故障 → 被隔离 → 其余团队仍产出，归并报告照常生成。"""
    agent = DeepResearchAgent(
        settings, llm=FakeLLM(), search_tool=_FailOneFocusSearch("子问题A"), workflow="teams"
    )
    report = await agent.run("测试问题")

    assert isinstance(report, Report)
    assert report.citations  # 故障团队被隔离，健康团队（子问题B）仍贡献来源
    assert any(e.stage == "AGGREGATOR" for e in agent.tracer.events)
