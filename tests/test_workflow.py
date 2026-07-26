"""验证声明式工作流引擎与可扩展性：

- 不同 workflow 走不同流程（deep vs quick vs reviewed）
- 新增 Critic 角色 + reviewed 流程：未改引擎/编排器即可被调度并发出自有事件
- 角色注册表按名解析
"""

from __future__ import annotations

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.agents.critic import Critique
from deep_research.models import Report
from deep_research.observability import Tracer
from deep_research.orchestrator import DeepResearchAgent
from deep_research.registry import available, create
from deep_research.workflow import Step, Workflow, WorkflowEngine, validate_workflow
from tests.fakes import FakeLLM, FakeSearch


class CriticAwareLLM(FakeLLM):
    """在 FakeLLM 基础上支持 Critique schema，让 Critic 角色可端到端运行。"""

    async def parse(self, system, user, schema, *, temperature=0.2, retries=2):
        if schema is Critique:
            return Critique(overall="可接受", issues=["X 论据偏弱"], suggestions=["补充 Y"])
        return await super().parse(system, user, schema, temperature=temperature, retries=retries)


@pytest.mark.asyncio
async def test_registry_resolves_builtin_roles():
    for name in ("planner", "researcher", "reflector", "synthesizer", "critic"):
        assert name in available()
        assert create(name).name == name


@pytest.mark.asyncio
async def test_quick_workflow_skips_reflection(settings):
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch(), workflow="quick")
    report = await agent.run("测试问题")
    assert isinstance(report, Report)
    stages = {e.stage for e in agent.tracer.events}
    assert "REFLECTOR" not in stages  # quick 流程不含反思
    assert {"PLANNER", "RESEARCHER", "SYNTHESIZER"} <= stages


@pytest.mark.asyncio
async def test_deep_workflow_includes_reflection(settings):
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch(), workflow="deep")
    await agent.run("测试问题")
    stages = {e.stage for e in agent.tracer.events}
    assert {"PLANNER", "RESEARCHER", "REFLECTOR", "SYNTHESIZER"} <= stages


@pytest.mark.asyncio
async def test_reviewed_workflow_runs_new_critic_role(settings):
    """新增角色 + 新增流程：引擎/编排器零改动即可调度，并发出自有 CRITIC 事件。"""
    agent = DeepResearchAgent(
        settings, llm=CriticAwareLLM(), search_tool=FakeSearch(), workflow="reviewed"
    )
    report = await agent.run("测试问题")
    assert isinstance(report, Report)
    stages = {e.stage for e in agent.tracer.events}
    assert "CRITIC" in stages  # 开放 Stage 枚举后，新角色能发自己的事件
    critic_done = [e for e in agent.tracer.events if e.stage == "CRITIC" and e.type == "info"]
    assert critic_done and critic_done[-1].data["issues"]


@pytest.mark.asyncio
async def test_unknown_workflow_falls_back_to_default(settings):
    agent = DeepResearchAgent(
        settings, llm=FakeLLM(), search_tool=FakeSearch(), workflow="does-not-exist"
    )
    await agent.run("测试问题")
    stages = {e.stage for e in agent.tracer.events}
    assert {"PLANNER", "RESEARCHER", "REFLECTOR", "SYNTHESIZER"} <= stages  # 回退到 deep


def test_validate_workflow_rules_and_custom_terminal():
    """校验器：拒未注册角色 / 无终端 / 嵌套 compose；terminal_roles 可把自定义角色计为终端。"""
    avail = {"planner", "researcher", "synthesizer", "my_writer"}

    # 合法：以内置 synthesizer 收尾
    ok = [Step(kind="agent", agent="researcher"), Step(kind="agent", agent="synthesizer")]
    assert validate_workflow(ok, avail, max_rounds_cap=2) == []

    # 引用未注册角色
    assert validate_workflow(
        [Step(kind="agent", agent="ghost"), Step(kind="agent", agent="synthesizer")],
        avail,
        max_rounds_cap=2,
    )

    # 缺终端角色
    assert any(
        "终端" in e
        for e in validate_workflow([Step(kind="agent", agent="planner")], avail, max_rounds_cap=2)
    )

    # 报告角色存在但不收尾：会在研究结果写入前生成空报告
    wrong_order = [Step(agent="synthesizer"), Step(agent="researcher")]
    assert any(
        "收尾" in e for e in validate_workflow(wrong_order, avail, max_rounds_cap=2)
    )

    # 禁止顶层编排原语出现在自建流程里
    assert validate_workflow([Step(kind="compose", agent="coordinator")], avail, max_rounds_cap=2)

    # 自定义 synthesize 卡片经 terminal_roles 计为终端 → 通过
    custom = [Step(kind="agent", agent="researcher"), Step(kind="agent", agent="my_writer")]
    assert validate_workflow(custom, avail, max_rounds_cap=2) != []  # 默认不认 my_writer
    assert validate_workflow(custom, avail, max_rounds_cap=2, terminal_roles={"my_writer"}) == []


def test_validate_workflow_caps_retry_budget():
    """字段级合法（max_attempts≤10、retry_backoff≤60）也不许组合出小时级最坏退避。"""
    avail = {"researcher", "synthesizer"}

    # 单步重试次数超上限
    too_many = [
        Step(agent="researcher", max_attempts=10, retry_backoff=0),
        Step(agent="synthesizer"),
    ]
    assert any("重试次数" in e for e in validate_workflow(too_many, avail, max_rounds_cap=2))

    # 全流程最坏退避总时长超上限：30 * (2^4 - 1) = 450s > 120s
    too_long = [
        Step(agent="researcher", max_attempts=5, retry_backoff=30),
        Step(agent="synthesizer"),
    ]
    assert any("退避总时长" in e for e in validate_workflow(too_long, avail, max_rounds_cap=2))

    # 合理的重试配置照常通过：5 * (2^2 - 1) = 15s ≤ 120s
    ok = [
        Step(agent="researcher", max_attempts=3, retry_backoff=5),
        Step(agent="synthesizer"),
    ]
    assert validate_workflow(ok, avail, max_rounds_cap=2) == []


@pytest.mark.asyncio
async def test_step_named_orchestrator_error_does_not_use_reserved_stage(settings):
    """用户角色卡可叫 "orchestrator"：其失败事件不得占用保留 Stage（运行终态判定）。"""

    class BrokenOrchestratorCard:
        name = "orchestrator"

        async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
            raise RuntimeError("card failure")

    tracer = Tracer()
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=tracer, settings=settings)
    engine = WorkflowEngine(
        ctx, resolver={"orchestrator": BrokenOrchestratorCard()}.__getitem__
    )

    await engine.run(
        Workflow(name="user-card", steps=[Step(agent="orchestrator")]),
        Blackboard(query="Q"),
    )

    errors = [e for e in tracer.events if e.type == "error"]
    assert errors  # 单步失败被隔离并记录……
    assert all(e.stage != "ORCHESTRATOR" for e in errors)  # ……但不冒充运行终态 Stage
    assert any(e.stage == "STEP_ORCHESTRATOR" for e in errors)
