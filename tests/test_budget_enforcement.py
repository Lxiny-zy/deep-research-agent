"""预算受限引擎：token 预算耗尽时跳过研究/反思，但仍综合产出尽力而为的报告（不崩）。

FakeLLM 默认不计 token，故现有测试预算永不触发；这里用会计入 token 的假实现来逼近真实。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from deep_research.models import Report
from deep_research.orchestrator import DeepResearchAgent
from tests.fakes import FakeLLM, FakeSearch


class BudgetBurnerLLM(FakeLLM):
    """每次 parse/stream 都向 tracer 计入大量 token，用于触发预算耗尽。

    真实 LLM 自带 tracer 并在调用处计 token；假实现需由测试把 agent.tracer 注入进来。
    """

    def __init__(self, charge: int = 10_000) -> None:
        super().__init__()
        self.tracer = None  # 由测试注入 agent.tracer
        self._charge = charge

    async def parse(self, system, user, schema, *, temperature=0.2, retries=2):  # type: ignore[no-untyped-def]
        if self.tracer is not None:
            self.tracer.add_tokens(self._charge)
        return await super().parse(system, user, schema, temperature=temperature, retries=retries)

    async def stream(
        self, system: str, user: str, *, temperature: float = 0.4
    ) -> AsyncIterator[str]:
        if self.tracer is not None:
            self.tracer.add_tokens(self._charge)
        async for piece in super().stream(system, user, temperature=temperature):
            yield piece


@pytest.mark.asyncio
async def test_budget_exhaustion_yields_partial_report(settings) -> None:
    settings.max_tokens = 5_000  # 小于单次 parse 的 10k → planner 跑完即耗尽
    llm = BudgetBurnerLLM(charge=10_000)
    agent = DeepResearchAgent(settings, llm=llm, search_tool=FakeSearch(), workflow="deep")
    llm.tracer = agent.tracer  # 注入 tracer（唯一真相源）

    report = await agent.run("测试问题")

    assert isinstance(report, Report)  # 不抛异常，仍产出报告
    stages = {e.stage for e in agent.tracer.events}
    assert "RESEARCHER" not in stages  # 预算在 planner 后耗尽 → 研究被跳过
    assert any(e.type == "info" and "预算" in e.message for e in agent.tracer.events)
    assert any(
        e.stage == "ORCHESTRATOR" and e.type == "done" for e in agent.tracer.events
    )  # 仍走到终态


@pytest.mark.asyncio
async def test_no_budget_runs_full_pipeline(settings) -> None:
    """max_tokens=None（默认）：预算不生效，完整流程照跑（回归保护）。"""
    assert settings.max_tokens is None
    llm = BudgetBurnerLLM(charge=10_000)
    agent = DeepResearchAgent(settings, llm=llm, search_tool=FakeSearch(), workflow="deep")
    llm.tracer = agent.tracer
    await agent.run("测试问题")
    stages = {e.stage for e in agent.tracer.events}
    assert {"PLANNER", "RESEARCHER", "SYNTHESIZER"} <= stages
