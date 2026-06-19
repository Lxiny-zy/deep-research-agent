"""Phase D 评估对照的离线测试：用假 LLM/检索 + 假评审跑通 run_comparison / format_comparison。

只验证对照「机制」（行数、token 列、汇总渲染），不依赖网络，也不断言真实质量分数。
"""

from __future__ import annotations

import pytest

from deep_research.config import Settings
from deep_research.orchestrator import DeepResearchAgent
from eval.dataset import CASES
from eval.judge import EvalScore
from eval.run_eval import format_comparison, run_comparison
from tests.fakes import FakeLLM, FakeSearch


class FakeJudge:
    async def score(self, query: str, markdown: str, notes: str = "") -> EvalScore:
        return EvalScore(coverage=4, groundedness=4, depth=3, coherence=5, justification="ok")


def _factory(settings: Settings, workflow: str) -> DeepResearchAgent:
    return DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch(), workflow=workflow)


@pytest.mark.asyncio
async def test_run_comparison_collects_rows_per_workflow(settings) -> None:
    rows = await run_comparison(
        settings, FakeJudge(), CASES[:1], ["deep", "quick"], agent_factory=_factory
    )
    assert len(rows) == 2  # 1 用例 × 2 工作流
    assert {r.workflow for r in rows} == {"deep", "quick"}
    assert all(r.tokens >= 0 for r in rows)  # token 列已填充（假实现为 0）


def test_format_comparison_renders_summary_and_delta() -> None:
    from eval.run_eval import EvalRow

    score = EvalScore(coverage=4, groundedness=4, depth=4, coherence=4)
    rows = [
        EvalRow("c1", "deep", score, 12000),
        EvalRow("c1", "auto", score, 8000),
    ]
    table = format_comparison(rows, ["deep", "auto"])
    assert "工作流汇总" in table
    assert "deep" in table and "auto" in table
    assert "对照：auto 相对 deep" in table
    assert "token -33%" in table  # (8000-12000)/12000 = -33%
