"""批量评估 + 工作流对照：对用例集逐条研究并打分，横向对比不同工作流的质量与成本。

不止「能产出报告」，还能量化「动态自组合 vs 静态深度流程」的质量/成本取舍——
这是把编排能力变成可写进简历的工程结论的关键一步。

运行：
  python -m eval.run_eval                          # 默认对比 deep vs auto
  python -m eval.run_eval --workflows deep,quick,auto
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from deep_research.config import Settings
from deep_research.orchestrator import DeepResearchAgent

from .dataset import CASES, EvalCase
from .judge import EvalScore, Judge

# 可注入的 agent 工厂：生产用真实 DeepResearchAgent；测试注入假 LLM/检索以离线跑通对照逻辑。
AgentFactory = Callable[[Settings, str], DeepResearchAgent]
# 评审协议：只需 score(query, markdown, notes) -> EvalScore，便于测试用假评审替换。
Scorer = Callable[[str, str, str], "object"]


@dataclass
class EvalRow:
    case_id: str
    workflow: str
    score: EvalScore
    tokens: int


def _default_agent_factory(settings: Settings, workflow: str) -> DeepResearchAgent:
    return DeepResearchAgent(settings, workflow=workflow)


async def run_comparison(
    settings: Settings,
    judge: object,
    cases: list[EvalCase],
    workflow_names: list[str],
    *,
    agent_factory: AgentFactory = _default_agent_factory,
    progress: Callable[[EvalCase, str], None] | None = None,
) -> list[EvalRow]:
    """对每个 workflow × case 跑研究 + 打分，捕获该次研究的累计 token，返回明细行。"""
    rows: list[EvalRow] = []
    for wf in workflow_names:
        for case in cases:
            if progress is not None:
                progress(case, wf)
            agent = agent_factory(settings, wf)
            try:
                report = await agent.run(case.query)
                score = await judge.score(case.query, report.markdown, case.notes)  # type: ignore[attr-defined]
                rows.append(EvalRow(case.id, wf, score, agent.tracer.total_tokens))
            finally:
                await agent.aclose()
    return rows


def _pct(value: float, base: float) -> str:
    if base == 0:
        return "n/a"
    return f"{(value - base) / base * 100:+.0f}%"


def format_comparison(rows: list[EvalRow], workflow_names: list[str]) -> str:
    """渲染逐用例明细 + 工作流汇总；两个以上工作流时给出相对基线的质量/成本差。"""
    lines: list[str] = ["=== 逐用例明细 ==="]
    header = (
        f"{'用例':<22}{'流程':<8}{'覆盖':>5}{'可靠':>5}"
        f"{'深度':>5}{'可读':>5}{'均分':>7}{'token':>9}"
    )
    lines.append(header)
    for r in rows:
        s = r.score
        lines.append(
            f"{r.case_id:<22}{r.workflow:<8}{s.coverage:>5}{s.groundedness:>5}"
            f"{s.depth:>5}{s.coherence:>5}{s.average:>7}{r.tokens:>9}"
        )

    lines += ["", "=== 工作流汇总（均分 / 均 token）===", f"{'流程':<8}{'均分':>8}{'均token':>10}"]
    summary: dict[str, tuple[float, float]] = {}
    for wf in workflow_names:
        wf_rows = [r for r in rows if r.workflow == wf]
        if not wf_rows:
            continue
        avg_score = round(sum(r.score.average for r in wf_rows) / len(wf_rows), 2)
        avg_tokens = round(sum(r.tokens for r in wf_rows) / len(wf_rows), 1)
        summary[wf] = (avg_score, avg_tokens)
        lines.append(f"{wf:<8}{avg_score:>8}{avg_tokens:>10}")

    if len(workflow_names) >= 2 and all(w in summary for w in workflow_names[:2]):
        base, other = workflow_names[0], workflow_names[1]
        bs, bt = summary[base]
        os_, ot = summary[other]
        lines += [
            "",
            f"对照：{other} 相对 {base} → 质量 {_pct(os_, bs)}，token {_pct(ot, bt)}",
        ]
    return "\n".join(lines)


async def _amain() -> None:
    parser = argparse.ArgumentParser(description="批量评估 + 工作流对照")
    parser.add_argument(
        "--workflows", default="deep,auto", help="逗号分隔的工作流名（默认 deep,auto）"
    )
    args = parser.parse_args()
    workflow_names = [w.strip() for w in args.workflows.split(",") if w.strip()]

    settings = Settings()
    settings.validate()
    judge = Judge(settings)

    print(f"▶ 对照工作流：{', '.join(workflow_names)}（{len(CASES)} 用例）…")
    rows = await run_comparison(
        settings,
        judge,
        CASES,
        workflow_names,
        progress=lambda case, wf: print(f"  · [{wf}] {case.id}"),
    )
    print("\n" + format_comparison(rows, workflow_names))


if __name__ == "__main__":
    asyncio.run(_amain())
