"""L4 多团队并行（teams 流程）：planner 切分 → 各团队隔离并行研究 → aggregator 归并。

覆盖：正常 fan-out + 归并产出报告；单个团队检索故障被隔离、其余团队仍贡献结果。
"""

from __future__ import annotations

import pytest

from deep_research.guardrails import (
    ClaimConsistencyReport,
    SemanticEvidenceDecisionList,
)
from deep_research.models import Finding, FindingList, Report, ResearchPlan, Source, SubQuestion
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


class _ConflictAcrossTeamsLLM(FakeLLM):
    async def parse(self, system, user, schema, *, temperature=0.2, retries=2):
        if schema is ResearchPlan:
            return ResearchPlan(
                interpretation="test",
                sub_questions=[
                    SubQuestion(question="increase team"),
                    SubQuestion(question="decrease team"),
                ],
            )
        if schema is FindingList:
            if "Revenue decreased in 2025." in user:
                return FindingList(
                    findings=[
                        Finding(
                            statement="Revenue decreased in 2025.",
                            source_url="https://b.com",
                            evidence_quote="Revenue decreased in 2025.",
                        )
                    ]
                )
            return FindingList(
                findings=[
                    Finding(
                        statement="Revenue increased in 2025.",
                        source_url="https://a.com",
                        evidence_quote="Revenue increased in 2025.",
                    )
                ]
            )
        if schema is SemanticEvidenceDecisionList:
            indexes = [
                int(line.removeprefix("Index: "))
                for line in user.splitlines()
                if line.startswith("Index: ")
            ]
            return SemanticEvidenceDecisionList(
                decisions=[
                    {
                        "index": index,
                        "verdict": "supported",
                        "confidence": 0.95,
                        "reason": "fixture",
                    }
                    for index in indexes
                ]
            )
        if schema is ClaimConsistencyReport:
            claim_ids = [
                line.removeprefix("Claim ID: ")
                for line in user.splitlines()
                if line.startswith("Claim ID: ")
            ]
            if len(claim_ids) < 2:
                return ClaimConsistencyReport(contradictions=[])
            return ClaimConsistencyReport(
                contradictions=[
                    {
                        "left_claim_id": claim_ids[0],
                        "right_claim_id": claim_ids[1],
                        "confidence": 0.9,
                        "reason": "opposite revenue direction",
                    }
                ]
            )
        return await super().parse(system, user, schema, temperature=temperature, retries=retries)


class _RevenueConflictSearch(FakeSearch):
    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        if "decrease" in query:
            return [
                Source(
                    title="B",
                    url="https://b.com",
                    content="Revenue decreased in 2025.",
                )
            ]
        return [
            Source(
                title="A",
                url="https://a.com",
                content="Revenue increased in 2025.",
            )
        ]


@pytest.mark.asyncio
async def test_team_fanout_checks_consistency_after_merging_children(settings) -> None:
    agent = DeepResearchAgent(
        settings,
        llm=_ConflictAcrossTeamsLLM(),
        search_tool=_RevenueConflictSearch(),
        workflow="teams",
    )

    await agent.run("revenue trend")

    consistency_events = [
        event
        for event in agent.tracer.events
        if event.stage == "AGGREGATOR"
        and event.data is not None
        and event.data.get("category") == "claim_consistency"
    ]
    assert consistency_events
    assert consistency_events[-1].data is not None
    assert consistency_events[-1].data["counts"] == {"conflicted": 2}
