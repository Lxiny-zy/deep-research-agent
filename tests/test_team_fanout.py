"""L4 多团队并行（teams 流程）：planner 切分 → 各团队隔离并行研究 → aggregator 归并。

覆盖：正常 fan-out + 归并产出报告；单个团队检索故障被隔离、其余团队仍贡献结果。
"""

from __future__ import annotations

import asyncio

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.guardrails import (
    ClaimConsistencyReport,
    SemanticEvidenceDecisionList,
)
from deep_research.models import Finding, FindingList, Report, ResearchPlan, Source, SubQuestion
from deep_research.observability import Tracer
from deep_research.orchestrator import DeepResearchAgent
from deep_research.workflow import Step, Workflow, WorkflowEngine
from tests.fakes import FakeLLM, FakeSearch


class _CheckpointTeamAgent:
    def __init__(self, name: str) -> None:
        self.name = name

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        bb.scratch[self.name] = True
        return bb


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


class _ManyTeamsLLM(FakeLLM):
    """planner 切出 5 个子主题，制造超过 max_concurrency 的团队数。"""

    async def parse(self, system, user, schema, *, temperature=0.2, retries=2):
        if schema is ResearchPlan:
            return ResearchPlan(
                interpretation="t",
                sub_questions=[SubQuestion(question=f"子主题{i}") for i in range(5)],
            )
        return await super().parse(system, user, schema, temperature=temperature, retries=retries)


class _ConcurrencyProbeSearch(FakeSearch):
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.02)
            return await super().search(query, max_results=max_results)
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_team_fanout_reuses_run_level_concurrency_limit(settings) -> None:
    """5 个子团队并行：各团队内的检索共用 run 级信号量，总并发不超 max_concurrency。"""
    settings.max_concurrency = 2
    probe = _ConcurrencyProbeSearch()
    agent = DeepResearchAgent(
        settings, llm=_ManyTeamsLLM(), search_tool=probe, workflow="teams"
    )

    report = await agent.run("测试问题")

    assert isinstance(report, Report)  # 限流不改变产出（无死锁、报告照常生成）
    assert probe.peak <= settings.max_concurrency


@pytest.mark.asyncio
async def test_team_children_do_not_write_root_checkpoints(settings) -> None:
    checkpoints = []

    async def save_checkpoint(run):  # type: ignore[no-untyped-def]
        checkpoints.append(run.model_copy(deep=True))

    agents = {
        "researcher": _CheckpointTeamAgent("researcher"),
        "aggregator": _CheckpointTeamAgent("aggregator"),
    }
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    engine = WorkflowEngine(
        ctx,
        resolver=agents.__getitem__,
        checkpoint_sink=save_checkpoint,
    )
    workflow = Workflow(
        name="root-teams",
        steps=[Step(kind="team_fanout", aggregator="aggregator")],
    )
    bb = Blackboard(
        query="Q",
        scratch={"subtasks": [{"focus": "left"}, {"focus": "right"}]},
    )

    await engine.run(workflow, bb)

    assert checkpoints
    assert {run.definition["name"] for run in checkpoints} == {"root-teams"}


@pytest.mark.asyncio
async def test_nested_step_ids_do_not_hide_future_root_steps_on_resume(settings) -> None:
    checkpoints = []

    async def save_checkpoint(run):  # type: ignore[no-untyped-def]
        checkpoints.append(run.model_copy(deep=True))

    agents = {
        name: _CheckpointTeamAgent(name)
        for name in ("researcher", "aggregator", "final")
    }
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    workflow = Workflow(
        name="root-after-team",
        steps=[
            Step(kind="team_fanout", aggregator="aggregator"),
            Step(agent="final"),
        ],
    )
    first_engine = WorkflowEngine(
        ctx,
        resolver=agents.__getitem__,
        checkpoint_sink=save_checkpoint,
    )
    initial = Blackboard(query="Q", scratch={"subtasks": [{"focus": "left"}]})

    await first_engine.run(workflow, initial)

    after_fanout = checkpoints[0]
    assert "final" not in after_fanout.checkpoint["scratch"]
    assert any(step.node_id.startswith("nested-") for step in after_fanout.steps)

    resumed_ctx = RunContext(
        llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings
    )
    resumed_engine = WorkflowEngine(
        resumed_ctx,
        resolver=agents.__getitem__,
        resume_run=after_fanout,
    )
    resumed = Blackboard.model_validate(after_fanout.checkpoint)

    await resumed_engine.run(workflow, resumed)

    assert resumed.scratch["final"] is True
