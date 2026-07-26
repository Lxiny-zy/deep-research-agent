"""批量评估 + 工作流对照：对用例集逐条研究并打分，横向对比不同工作流的质量/成本/耗时。

不止「能产出报告」，还能量化「动态自组合 vs 静态深度流程」的质量/成本取舍——
这是把编排能力变成可写进简历的工程结论的关键一步。

维度：
  - 质量：LLM-as-judge 四维打分（覆盖/可靠/深度/可读）
  - 成本：单次研究累计 token（Tracer 真值）
  - 耗时：agent.run 的墙钟时间（time.monotonic）
  - 预算：--budget 透传 per-run token 上限（Settings.max_tokens → TokenBudget），
    可跑「同一工作流不同预算下的质量曲线」

运行：
  python -m eval.run_eval                                        # 默认对比 deep vs auto
  python -m eval.run_eval --workflows deep,quick,reviewed,auto,teams --output
  python -m eval.run_eval --workflows deep --budget 30000 --output eval/results/deep-30k.md
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from deep_research.config import Settings
from deep_research.orchestrator import DeepResearchAgent

from .dataset import CASES, EvalCase
from .judge import EvalScore, Judge

# 可注入的 agent 工厂：生产用真实 DeepResearchAgent；测试注入假 LLM/检索以离线跑通对照逻辑。
AgentFactory = Callable[[Settings, str], DeepResearchAgent]
# 评审协议：只需 score(query, markdown, notes) -> EvalScore，便于测试用假评审替换。
Scorer = Callable[[str, str, str], "object"]

# --output 不带值时的哨兵：写入 eval/results/<date>.md
_AUTO_OUTPUT = "__auto__"


@dataclass
class EvalRow:
    case_id: str
    workflow: str
    score: EvalScore
    tokens: int
    wall_seconds: float = 0.0  # agent.run 墙钟耗时（不含 judge 打分）
    budget: int | None = None  # 本次运行的 per-run token 预算（None＝不限）


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
    budget: int | None = None,
) -> list[EvalRow]:
    """对每个 workflow × case 跑研究 + 打分，捕获 token 与墙钟耗时，返回明细行。

    budget 非空时以 dataclasses.replace 覆盖 Settings.max_tokens 透传给每次运行
    （与 API 的 per-run params 同机制），引擎内部由 TokenBudget 执行预算门禁。
    """
    run_settings = replace(settings, max_tokens=budget) if budget is not None else settings
    rows: list[EvalRow] = []
    for wf in workflow_names:
        for case in cases:
            if progress is not None:
                progress(case, wf)
            agent = agent_factory(run_settings, wf)
            try:
                started = time.monotonic()
                report = await agent.run(case.query)
                wall = time.monotonic() - started
                score = await judge.score(case.query, report.markdown, case.notes)  # type: ignore[attr-defined]
                rows.append(
                    EvalRow(
                        case.id,
                        wf,
                        score,
                        agent.tracer.total_tokens,
                        wall_seconds=wall,
                        budget=budget,
                    )
                )
            finally:
                await agent.aclose()
    return rows


def _pct(value: float, base: float) -> str:
    if base == 0:
        return "n/a"
    return f"{(value - base) / base * 100:+.0f}%"


def _summarize(
    rows: list[EvalRow], workflow_names: list[str]
) -> dict[str, tuple[float, float, float]]:
    """按工作流汇总：均分 / 均 token / 均耗时（秒）。"""
    summary: dict[str, tuple[float, float, float]] = {}
    for wf in workflow_names:
        wf_rows = [r for r in rows if r.workflow == wf]
        if not wf_rows:
            continue
        n = len(wf_rows)
        summary[wf] = (
            round(sum(r.score.average for r in wf_rows) / n, 2),
            round(sum(r.tokens for r in wf_rows) / n, 1),
            round(sum(r.wall_seconds for r in wf_rows) / n, 1),
        )
    return summary


def _delta_lines(
    summary: dict[str, tuple[float, float, float]], workflow_names: list[str]
) -> list[str]:
    """每个非基线工作流相对首个工作流（基线）的质量/token/耗时相对差。"""
    if not workflow_names or workflow_names[0] not in summary:
        return []
    base = workflow_names[0]
    bs, bt, bw = summary[base]
    lines: list[str] = []
    for wf in workflow_names[1:]:
        if wf not in summary:
            continue
        s, t, w = summary[wf]
        lines.append(
            f"对照：{wf} 相对 {base} → 质量 {_pct(s, bs)}，"
            f"token {_pct(t, bt)}，耗时 {_pct(w, bw)}"
        )
    return lines


def format_comparison(rows: list[EvalRow], workflow_names: list[str]) -> str:
    """渲染逐用例明细 + 工作流汇总；两个以上工作流时给出相对基线的质量/成本/耗时差。"""
    lines: list[str] = ["=== 逐用例明细 ==="]
    header = (
        f"{'用例':<22}{'流程':<10}{'覆盖':>5}{'可靠':>5}"
        f"{'深度':>5}{'可读':>5}{'均分':>7}{'token':>9}{'耗时s':>8}"
    )
    lines.append(header)
    for r in rows:
        s = r.score
        lines.append(
            f"{r.case_id:<22}{r.workflow:<10}{s.coverage:>5}{s.groundedness:>5}"
            f"{s.depth:>5}{s.coherence:>5}{s.average:>7}{r.tokens:>9}{r.wall_seconds:>8.1f}"
        )

    lines += [
        "",
        "=== 工作流汇总（均分 / 均 token / 均耗时）===",
        f"{'流程':<10}{'均分':>8}{'均token':>10}{'均耗时s':>9}",
    ]
    summary = _summarize(rows, workflow_names)
    for wf, (avg_score, avg_tokens, avg_wall) in summary.items():
        lines.append(f"{wf:<10}{avg_score:>8}{avg_tokens:>10}{avg_wall:>9}")

    deltas = _delta_lines(summary, workflow_names)
    if deltas:
        lines += [""] + deltas
    return "\n".join(lines)


def format_markdown(
    rows: list[EvalRow],
    workflow_names: list[str],
    *,
    cases: list[EvalCase] | None = None,
    budget: int | None = None,
    run_date: str | None = None,
) -> str:
    """渲染 markdown 结果文档：元信息 + 明细表 + 汇总表 + 对照结论行。"""
    budget_text = str(budget) if budget is not None else "不限"
    md: list[str] = [
        f"# 编排对照实验结果（{run_date or date.today().isoformat()}）",
        "",
        f"- 工作流：{', '.join(workflow_names)}",
        f"- 单次运行 token 预算：{budget_text}",
    ]
    if cases:
        counts = Counter(c.category or "未分类" for c in cases)
        detail = "，".join(f"{cat} {n}" for cat, n in counts.items())
        md.append(f"- 用例：{len(cases)} 条（{detail}）")
    md += [
        "",
        "## 逐用例明细",
        "",
        "| 用例 | 流程 | 覆盖 | 可靠 | 深度 | 可读 | 均分 | token | 耗时(s) | 预算 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        s = r.score
        row_budget = str(r.budget) if r.budget is not None else "不限"
        md.append(
            f"| {r.case_id} | {r.workflow} | {s.coverage} | {s.groundedness} "
            f"| {s.depth} | {s.coherence} | {s.average} | {r.tokens} "
            f"| {r.wall_seconds:.1f} | {row_budget} |"
        )

    summary = _summarize(rows, workflow_names)
    md += [
        "",
        "## 工作流汇总",
        "",
        "| 流程 | 均分 | 均 token | 均耗时(s) |",
        "| --- | ---: | ---: | ---: |",
    ]
    for wf, (avg_score, avg_tokens, avg_wall) in summary.items():
        md.append(f"| {wf} | {avg_score} | {avg_tokens} | {avg_wall} |")

    md += ["", "## 对照结论", ""]
    deltas = _delta_lines(summary, workflow_names)
    if deltas:
        md += [f"- {line}" for line in deltas]
    else:
        md.append("- （不足两个工作流，无对照结论）")
    md.append("")
    return "\n".join(md)


def write_results(path: Path | str, content: str) -> Path:
    """把结果写入 markdown 文件；父目录不存在则创建。返回实际路径。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _resolve_output_path(raw: str) -> Path:
    if raw == _AUTO_OUTPUT:
        return Path(__file__).resolve().parent / "results" / f"{date.today().isoformat()}.md"
    return Path(raw)


async def _amain() -> None:
    parser = argparse.ArgumentParser(description="批量评估 + 工作流对照")
    parser.add_argument(
        "--workflows", default="deep,auto", help="逗号分隔的工作流名（默认 deep,auto）"
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=None,
        help="单次研究 token 预算上限（透传 Settings.max_tokens，默认不限）",
    )
    parser.add_argument(
        "--output",
        nargs="?",
        const=_AUTO_OUTPUT,
        default=None,
        metavar="PATH",
        help="把结果写入 markdown 文件；不带值时默认 eval/results/<date>.md",
    )
    args = parser.parse_args()
    workflow_names = [w.strip() for w in args.workflows.split(",") if w.strip()]
    if args.budget is not None and args.budget < 1:
        parser.error("--budget 必须 >= 1")

    settings = Settings()
    settings.validate()
    judge = Judge(settings)
    try:
        budget_text = str(args.budget) if args.budget is not None else "不限"
        print(
            f"▶ 对照工作流：{', '.join(workflow_names)}"
            f"（{len(CASES)} 用例，预算 {budget_text}）…"
        )
        rows = await run_comparison(
            settings,
            judge,
            CASES,
            workflow_names,
            progress=lambda case, wf: print(f"  · [{wf}] {case.id}"),
            budget=args.budget,
        )
        print("\n" + format_comparison(rows, workflow_names))
        if args.output is not None:
            markdown = format_markdown(
                rows, workflow_names, cases=CASES, budget=args.budget
            )
            target = write_results(_resolve_output_path(args.output), markdown)
            print(f"\n✔ 结果已写入 {target}")
    finally:
        await judge.aclose()


if __name__ == "__main__":
    asyncio.run(_amain())
