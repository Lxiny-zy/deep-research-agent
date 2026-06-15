"""验证声明式工作流引擎与可扩展性：

  - 不同 workflow 走不同流程（deep vs quick vs reviewed）
  - 新增 Critic 角色 + reviewed 流程：未改引擎/编排器即可被调度并发出自有事件
  - 角色注册表按名解析
"""

from __future__ import annotations

import pytest

from deep_research.agents.critic import Critique
from deep_research.models import Report
from deep_research.orchestrator import DeepResearchAgent
from deep_research.registry import available, create
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
    agent = DeepResearchAgent(
        settings, llm=FakeLLM(), search_tool=FakeSearch(), workflow="quick"
    )
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
