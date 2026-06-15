"""验证编排器的 DAG 调度：前驱先于后继、前驱发现作为上下文传递、环降级不死锁。"""

from __future__ import annotations

import pytest

from deep_research.models import Finding, ResearchResult, SubQuestion
from deep_research.orchestrator import DeepResearchAgent
from tests.fakes import FakeLLM, FakeSearch


class ProbeResearcher:
    """探针 Researcher：记录检索顺序与收到的上下文，并产出可识别的发现。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def run(self, sub_question, context_findings=None):
        self.calls.append((sub_question, [f.statement for f in (context_findings or [])]))
        return ResearchResult(
            sub_question=sub_question,
            findings=[
                Finding(statement=f"F::{sub_question}", source_url=f"https://{sub_question}.com")
            ],
        )


@pytest.mark.asyncio
async def test_dag_schedule_orders_and_passes_context(settings):
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch())
    probe = ProbeResearcher()
    agent.researcher = probe  # type: ignore[assignment]

    subs = [
        SubQuestion(question="A"),
        SubQuestion(question="B", depends_on=[0]),  # B 依赖 A
    ]
    results = await agent._research_dag(subs)

    # 拓扑顺序：A 必须先于 B 被检索
    order = [q for q, _ in probe.calls]
    assert order.index("A") < order.index("B")

    # B 应收到 A 的发现作为上下文
    b_ctx = next(ctx for q, ctx in probe.calls if q == "B")
    assert "F::A" in b_ctx

    assert {r.sub_question for r in results} == {"A", "B"}


@pytest.mark.asyncio
async def test_dag_cycle_degrades_to_parallel(settings):
    agent = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch())
    agent.researcher = ProbeResearcher()  # type: ignore[assignment]

    subs = [
        SubQuestion(question="A", depends_on=[1]),
        SubQuestion(question="B", depends_on=[0]),  # A ↔ B 成环
    ]
    results = await agent._research_dag(subs)

    # 破环降级后两个子问题都应被检索完成，不死锁、不丢失
    assert {r.sub_question for r in results} == {"A", "B"}
