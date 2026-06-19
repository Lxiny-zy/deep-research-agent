"""L3 自组合（auto 流程）：Coordinator 运行时生成流程 → 引擎递归执行 → 落库可读。

覆盖：正常自组合、生成不合法的自修复、生成嵌套 compose 的拒绝+回退（保证终止）、
以及 compose 复用同一黑板使落库语义不变。
"""

from __future__ import annotations

import pytest

from deep_research.models import Report
from deep_research.orchestrator import DeepResearchAgent
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.workflow import GeneratedWorkflow, Step
from tests.fakes import FakeLLM, FakeSearch


class AutoLLM(FakeLLM):
    """对 GeneratedWorkflow 返回预置流程，其余 schema 沿用 FakeLLM。"""

    def __init__(self, steps: list[Step] | None = None) -> None:
        super().__init__()
        self._steps = steps
        self.gw_calls = 0

    async def parse(self, system, user, schema, *, temperature=0.2, retries=2):  # type: ignore[no-untyped-def]
        if schema is GeneratedWorkflow:
            self.gw_calls += 1
            steps = (
                self._steps
                if self._steps is not None
                else [
                    Step(kind="agent", agent="planner"),
                    Step(kind="agent", agent="researcher"),
                    Step(kind="agent", agent="synthesizer"),
                ]
            )
            return GeneratedWorkflow(steps=steps)
        return await super().parse(system, user, schema, temperature=temperature, retries=retries)


@pytest.mark.asyncio
async def test_auto_workflow_composes_and_runs(settings) -> None:
    agent = DeepResearchAgent(settings, llm=AutoLLM(), search_tool=FakeSearch(), workflow="auto")
    report = await agent.run("测试问题")

    assert isinstance(report, Report)
    stages = {e.stage for e in agent.tracer.events}
    assert "COORDINATOR" in stages  # 开放 Stage 后新角色发自有事件
    assert {"PLANNER", "RESEARCHER", "SYNTHESIZER"} <= stages  # 执行了生成的流程
    # Coordinator 把生成的流程作为事件 data 透出，供前端可视化
    coord = [e for e in agent.tracer.events if e.stage == "COORDINATOR" and e.type == "info"]
    assert coord and coord[0].data and coord[0].data["steps"]


@pytest.mark.asyncio
async def test_auto_workflow_persists_via_shared_blackboard(settings) -> None:
    """compose 复用同一黑板：生成流程产出的计划/结果/报告照常落库。"""
    repo = InMemoryRepository()
    run_id = await repo.create_run("测试问题")
    agent = DeepResearchAgent(
        settings, llm=AutoLLM(), search_tool=FakeSearch(), workflow="auto", repo=repo, run_id=run_id
    )
    await agent.run("测试问题")

    detail = await repo.get_run(run_id)
    assert detail is not None
    assert detail.report is not None
    assert detail.results  # 研究结果已落库
    assert detail.sub_questions  # 计划已落库


@pytest.mark.asyncio
async def test_auto_workflow_self_repairs_invalid_plan(settings) -> None:
    """首个生成不合法（引用未注册角色且无终端）→ 回灌自修复一次 → 第二次合法。"""

    class RepairLLM(AutoLLM):
        async def parse(self, system, user, schema, *, temperature=0.2, retries=2):  # type: ignore[no-untyped-def]
            if schema is GeneratedWorkflow:
                self.gw_calls += 1
                if self.gw_calls == 1:
                    return GeneratedWorkflow(steps=[Step(kind="agent", agent="不存在的角色")])
                # 修复后的流程含研究步，确保有产出（避免触发 Phase B 的零产出重规划，隔离本用例）
                return GeneratedWorkflow(
                    steps=[
                        Step(kind="agent", agent="planner"),
                        Step(kind="agent", agent="researcher"),
                        Step(kind="agent", agent="synthesizer"),
                    ]
                )
            return await FakeLLM.parse(
                self, system, user, schema, temperature=temperature, retries=retries
            )

    llm = RepairLLM()
    agent = DeepResearchAgent(settings, llm=llm, search_tool=FakeSearch(), workflow="auto")
    report = await agent.run("测试问题")

    assert isinstance(report, Report)
    assert llm.gw_calls == 2  # 恰好一次自修复
    assert any(
        e.stage == "COORDINATOR" and e.type == "info" and "自修复" in e.message
        for e in agent.tracer.events
    )


@pytest.mark.asyncio
async def test_auto_workflow_rejects_nested_compose_and_terminates(settings) -> None:
    """生成里塞入 compose（自指）→ 校验拒绝 → 回退 deep → 仍产出报告且不无限递归。"""
    llm = AutoLLM(steps=[Step(kind="compose", agent="coordinator")])
    agent = DeepResearchAgent(settings, llm=llm, search_tool=FakeSearch(), workflow="auto")
    report = await agent.run("测试问题")

    assert isinstance(report, Report)
    stages = {e.stage for e in agent.tracer.events}
    assert "SYNTHESIZER" in stages  # 回退 deep 后仍走到综合


@pytest.mark.asyncio
async def test_auto_workflow_replans_on_zero_findings(settings) -> None:
    """首个生成只含 synthesizer（零检索→零产出）→ 有界重规划一次 → 第二次含研究并产出。"""

    class ReplanLLM(AutoLLM):
        async def parse(self, system, user, schema, *, temperature=0.2, retries=2):  # type: ignore[no-untyped-def]
            if schema is GeneratedWorkflow:
                self.gw_calls += 1
                if self.gw_calls == 1:
                    return GeneratedWorkflow(steps=[Step(kind="agent", agent="synthesizer")])
                return GeneratedWorkflow(
                    steps=[
                        Step(kind="agent", agent="planner"),
                        Step(kind="agent", agent="researcher"),
                        Step(kind="agent", agent="synthesizer"),
                    ]
                )
            return await FakeLLM.parse(
                self, system, user, schema, temperature=temperature, retries=retries
            )

    settings.max_replans = 1
    llm = ReplanLLM()
    agent = DeepResearchAgent(settings, llm=llm, search_tool=FakeSearch(), workflow="auto")
    report = await agent.run("测试问题")

    assert isinstance(report, Report)
    assert llm.gw_calls == 2  # 恰好一次重规划
    assert report.citations  # 第二次流程做了研究，报告有引用
    assert any(
        e.stage == "COORDINATOR" and e.type == "round" for e in agent.tracer.events
    )  # 重规划事件已透出
