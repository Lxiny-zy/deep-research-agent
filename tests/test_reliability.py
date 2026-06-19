"""可靠性：单步整体失败被引擎隔离（记一条该步 Stage 的 error 后继续），
流程仍走到终端综合并完成（区别于 researcher 内部按子问题的隔离）。
"""

from __future__ import annotations

import pytest

from deep_research.models import Reflection, Report
from deep_research.orchestrator import DeepResearchAgent
from tests.fakes import FakeLLM, FakeSearch


class FailingReflectorLLM(FakeLLM):
    """反思阶段整体抛异常（重试预算耗尽后真的炸出来）。"""

    async def parse(self, system, user, schema, *, temperature=0.2, retries=2):  # type: ignore[no-untyped-def]
        if schema is Reflection:
            raise RuntimeError("反思阶段瞬时故障")
        return await super().parse(system, user, schema, temperature=temperature, retries=retries)


@pytest.mark.asyncio
async def test_step_failure_is_isolated_and_run_completes(settings) -> None:
    agent = DeepResearchAgent(
        settings, llm=FailingReflectorLLM(), search_tool=FakeSearch(), workflow="deep"
    )
    report = await agent.run("测试问题")

    assert isinstance(report, Report)
    # 反思步失败被隔离为一条 REFLECTOR error 事件（非 ORCHESTRATOR，故不会让 run_stream 断流）
    assert any(e.stage == "REFLECTOR" and e.type == "error" for e in agent.tracer.events)
    # ……但流程仍走到综合并以 done 收尾，且报告保留反思前已产出的引用
    assert any(e.stage == "SYNTHESIZER" for e in agent.tracer.events)
    assert any(e.stage == "ORCHESTRATOR" and e.type == "done" for e in agent.tracer.events)
    assert report.citations


@pytest.mark.asyncio
async def test_isolated_failure_does_not_break_stream(settings) -> None:
    """单步 error 不是运行终态：run_stream 不得提前断，仍走到 ORCHESTRATOR done。"""
    agent = DeepResearchAgent(
        settings, llm=FailingReflectorLLM(), search_tool=FakeSearch(), workflow="deep"
    )
    events = [ev async for ev in agent.run_stream("测试问题")]
    assert any(e.stage == "REFLECTOR" and e.type == "error" for e in events)
    assert events[-1].stage == "ORCHESTRATOR"
    assert events[-1].type == "done"
