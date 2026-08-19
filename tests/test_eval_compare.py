"""Phase D 评估对照的离线测试：用假 LLM/检索 + 假评审跑通 run_comparison / format_comparison。

只验证对照「机制」（行数、token/耗时/预算列、汇总与 markdown 渲染），不依赖网络，
也不断言真实质量分数。真实数字由用户以真实 LLM judge 自行跑出。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from deep_research.config import Settings
from deep_research.orchestrator import DeepResearchAgent
from deep_research.persistence.memory_repository import InMemoryRepository
from eval.dataset import CASES, CATEGORY_FACT, CATEGORY_FRESH, CATEGORY_MULTI
from eval.judge import EvalScore
from eval.run_eval import (
    EvalRow,
    benchmark_payload,
    format_comparison,
    format_markdown,
    run_comparison,
    write_results,
)
from tests.fakes import FakeLLM, FakeSearch


class FakeJudge:
    async def score(self, query: str, markdown: str, notes: str = "") -> EvalScore:
        return EvalScore(coverage=4, groundedness=4, depth=3, coherence=5, justification="ok")


def _factory(settings: Settings, workflow: str) -> DeepResearchAgent:
    return DeepResearchAgent(settings, llm=FakeLLM(), search_tool=FakeSearch(), workflow=workflow)


class _StubAgent:
    """最小 agent 桩：只提供 run/tracer/aclose，用于验证耗时与预算透传机制。"""

    def __init__(self, delay: float = 0.0, tokens: int = 1234) -> None:
        self._delay = delay
        self.tracer = SimpleNamespace(total_tokens=tokens)

    async def run(self, query: str):  # noqa: ANN201 - 鸭子类型报告对象
        if self._delay:
            await asyncio.sleep(self._delay)
        return SimpleNamespace(markdown="# 报告")

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_run_comparison_collects_rows_per_workflow(settings) -> None:
    rows = await run_comparison(
        settings, FakeJudge(), CASES[:1], ["deep", "quick"], agent_factory=_factory
    )
    assert len(rows) == 2  # 1 用例 × 2 工作流
    assert {r.workflow for r in rows} == {"deep", "quick"}
    assert all(r.tokens >= 0 for r in rows)  # token 列已填充（假实现为 0）
    assert all(r.wall_seconds >= 0.0 for r in rows)  # 墙钟列已填充
    assert all(r.budget is None for r in rows)  # 未指定预算时保持 None


@pytest.mark.asyncio
async def test_run_comparison_records_wall_clock(settings) -> None:
    delay = 0.03

    def factory(_settings: Settings, _workflow: str):
        return _StubAgent(delay=delay)

    rows = await run_comparison(settings, FakeJudge(), CASES[:1], ["deep"], agent_factory=factory)
    # 墙钟应至少覆盖 agent.run 内的 sleep（留计时器精度余量）
    assert rows[0].wall_seconds >= delay * 0.5


@pytest.mark.asyncio
async def test_run_comparison_passes_budget_through_settings(settings) -> None:
    seen: list[Settings] = []

    def factory(run_settings: Settings, _workflow: str):
        seen.append(run_settings)
        return _StubAgent()

    rows = await run_comparison(
        settings, FakeJudge(), CASES[:2], ["deep"], agent_factory=factory, budget=5000
    )
    # 预算经 Settings.max_tokens 透传给每次运行（与 API per-run params 同机制）
    assert all(s.max_tokens == 5000 for s in seen)
    assert all(r.budget == 5000 for r in rows)
    # 原 settings 不被就地修改（replace 生成副本）
    assert settings.max_tokens != 5000 or seen[0] is not settings


@pytest.mark.asyncio
async def test_run_comparison_without_budget_keeps_settings(settings) -> None:
    seen: list[Settings] = []

    def factory(run_settings: Settings, _workflow: str):
        seen.append(run_settings)
        return _StubAgent()

    await run_comparison(settings, FakeJudge(), CASES[:1], ["deep"], agent_factory=factory)
    assert seen[0] is settings  # 不指定预算时直接沿用原 settings


@pytest.mark.asyncio
async def test_run_comparison_persists_traceable_run(settings) -> None:
    repo = InMemoryRepository()

    def persistent_factory(run_settings, workflow, repository, run_id, execution):
        return DeepResearchAgent(
            run_settings,
            llm=FakeLLM(),
            search_tool=FakeSearch(),
            repo=repository,
            run_id=run_id,
            workflow=workflow,
            initial_execution=execution,
        )

    rows = await run_comparison(
        settings,
        FakeJudge(),
        CASES[:1],
        ["deep"],
        repository=repo,
        persistent_agent_factory=persistent_factory,
    )

    row = rows[0]
    detail = await repo.get_run(row.run_id)
    assert detail is not None
    assert detail.status == "done"
    assert row.manifest is not None
    assert row.manifest.workflow_name == "deep"
    assert row.metrics is not None
    assert row.metrics.source_snapshots >= 1


def test_format_comparison_renders_summary_and_delta() -> None:
    score = EvalScore(coverage=4, groundedness=4, depth=4, coherence=4)
    rows = [
        EvalRow("c1", "deep", score, 12000, wall_seconds=20.0),
        EvalRow("c1", "auto", score, 8000, wall_seconds=10.0),
    ]
    table = format_comparison(rows, ["deep", "auto"])
    assert "工作流汇总" in table
    assert "deep" in table and "auto" in table
    assert "耗时" in table  # 明细与汇总带耗时列
    assert "对照：auto 相对 deep" in table
    assert "token -33%" in table  # (8000-12000)/12000 = -33%
    assert "耗时 -50%" in table  # (10-20)/20 = -50%


def test_format_markdown_renders_tables_and_conclusion() -> None:
    score = EvalScore(coverage=4, groundedness=4, depth=4, coherence=4)
    rows = [
        EvalRow("c1", "deep", score, 12000, wall_seconds=20.0, budget=30000),
        EvalRow("c1", "quick", score, 6000, wall_seconds=5.0, budget=30000),
    ]
    md = format_markdown(rows, ["deep", "quick"], cases=CASES, budget=30000, run_date="2026-07-26")
    assert "# 编排对照实验结果（2026-07-26）" in md
    assert "单次运行 token 预算：30000" in md
    assert f"用例：{len(CASES)} 条" in md
    assert "| c1 | deep | - | 4 | 4 | 4 | 4 | 4.0 | n/a | n/a | n/a | n/a | 12000" in md
    assert "## 工作流汇总" in md
    assert "## 对照结论" in md
    assert "quick 相对 deep" in md and "耗时 -75%" in md


def test_format_markdown_single_workflow_has_no_delta() -> None:
    score = EvalScore(coverage=3, groundedness=3, depth=3, coherence=3)
    rows = [EvalRow("c1", "deep", score, 1000, wall_seconds=1.0)]
    md = format_markdown(rows, ["deep"], run_date="2026-07-26")
    assert "无对照结论" in md
    assert "预算：不限" in md


def test_write_results_creates_missing_directories(tmp_path) -> None:
    target = tmp_path / "results" / "nested" / "2026-07-26.md"
    written = write_results(target, "# 结果\n")
    assert written == target
    assert target.read_text(encoding="utf-8") == "# 结果\n"


def test_benchmark_payload_is_machine_readable_and_hashed() -> None:
    score = EvalScore(coverage=4, groundedness=4, depth=3, coherence=5)
    row = EvalRow("c1", "deep", score, 100, run_id="run-1")
    payload = benchmark_payload(
        [row], ["deep"], cases=CASES[:1], budget=5000, run_date="2026-07-28"
    )

    assert payload["schema_version"] == 1
    assert payload["rows"][0]["run_id"] == "run-1"
    assert payload["dataset"] == [CASES[0].id]
    assert len(payload["dataset_sha256"]) == 64
    assert len(payload["rows_sha256"]) == 64


def test_dataset_covers_three_categories_with_enough_cases() -> None:
    assert len(CASES) >= 10
    assert len({c.id for c in CASES}) == len(CASES)  # id 唯一
    categories = {c.category for c in CASES}
    assert {CATEGORY_FACT, CATEGORY_MULTI, CATEGORY_FRESH} <= categories
    assert all(c.query and c.notes for c in CASES)  # 每条都有判分要点


@pytest.mark.asyncio
async def test_judge_aclose_closes_underlying_llm(settings) -> None:
    from eval.judge import Judge

    # OpenAI >=2.54 rejects an empty key during client construction even though
    # this test never sends a request; use an explicit non-secret test value.
    judge = Judge(replace(settings, llm_api_key="test-key"))
    real_llm = judge.llm

    closed = False

    class _ClosableLLM:
        async def aclose(self) -> None:
            nonlocal closed
            closed = True

    judge.llm = _ClosableLLM()  # type: ignore[assignment]
    await judge.aclose()  # aclose 必须转发到底层 LLM，否则连接池泄漏
    assert closed is True

    await real_llm.aclose()  # 清理测试中真实构造的客户端
