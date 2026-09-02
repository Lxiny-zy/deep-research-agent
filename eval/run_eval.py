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
  # 评估公共模板与内部编排策略
  python -m eval.run_eval --workflows deep,quick,reviewed,auto,teams --output
  python -m eval.run_eval --workflows deep --budget 30000 --output eval/results/deep-30k.md
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from deep_research.config import Settings
from deep_research.models import QualityMetrics, RunManifest
from deep_research.orchestrator import (
    DeepResearchAgent,
    create_initial_execution,
)
from deep_research.persistence.db import make_engine, make_sessionmaker, prepare_sqlite_schema
from deep_research.persistence.repository import ResearchRepository
from deep_research.persistence.sql_repository import SqlRepository
from deep_research.reproducibility import RUN_MANIFEST_CHECKPOINT_KEY, quality_metrics

from .dataset import CASES, EvalCase
from .judge import EvalScore, Judge
from .regression import (
    RegressionPolicy,
    evaluate_regression,
    format_regression,
    load_benchmark,
)

# 可注入的 agent 工厂：生产用真实 DeepResearchAgent；测试注入假 LLM/检索以离线跑通对照逻辑。
AgentFactory = Callable[[Settings, str], DeepResearchAgent]
PersistentAgentFactory = Callable[
    [Settings, str, ResearchRepository, str, object], DeepResearchAgent
]
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
    run_id: str = ""
    manifest: RunManifest | None = None
    metrics: QualityMetrics | None = None


def _default_agent_factory(settings: Settings, workflow: str) -> DeepResearchAgent:
    return DeepResearchAgent(settings, workflow=workflow)


def _default_persistent_agent_factory(
    settings: Settings,
    workflow: str,
    repository: ResearchRepository,
    run_id: str,
    execution: object,
) -> DeepResearchAgent:
    from deep_research.orchestration import WorkflowRun

    initial_execution = WorkflowRun.model_validate(execution)
    return DeepResearchAgent(
        settings,
        repo=repository,
        run_id=run_id,
        workflow=workflow,
        initial_execution=initial_execution,
    )


async def run_comparison(
    settings: Settings,
    judge: object,
    cases: list[EvalCase],
    workflow_names: list[str],
    *,
    agent_factory: AgentFactory = _default_agent_factory,
    progress: Callable[[EvalCase, str], None] | None = None,
    budget: int | None = None,
    repository: ResearchRepository | None = None,
    persistent_agent_factory: PersistentAgentFactory = _default_persistent_agent_factory,
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
            run_id = ""
            if repository is None:
                agent = agent_factory(run_settings, wf)
            else:
                execution = create_initial_execution(case.query, wf, run_settings)
                run_id = await repository.create_run(case.query, execution=execution)
                agent = persistent_agent_factory(run_settings, wf, repository, run_id, execution)
            try:
                started = time.monotonic()
                report = await agent.run(case.query)
                wall = time.monotonic() - started
                score = await judge.score(case.query, report.markdown, case.notes)  # type: ignore[attr-defined]
                row = EvalRow(
                    case.id,
                    wf,
                    score,
                    agent.tracer.total_tokens,
                    wall_seconds=wall,
                    budget=budget,
                    run_id=run_id,
                )
                if repository is not None:
                    detail = await repository.get_run(run_id)
                    if detail is None:
                        raise RuntimeError(f"persisted benchmark run disappeared: {run_id}")
                    detail.events = await repository.get_events(run_id)
                    scratch = (
                        detail.orchestration.checkpoint.get("scratch", {})
                        if detail.orchestration is not None
                        else {}
                    )
                    raw_manifest = (
                        scratch.get(RUN_MANIFEST_CHECKPOINT_KEY)
                        if isinstance(scratch, dict)
                        else None
                    )
                    if raw_manifest is not None:
                        row.manifest = RunManifest.model_validate(raw_manifest)
                    row.metrics = quality_metrics(
                        detail,
                        require_corroboration=run_settings.require_corroboration,
                    )
                rows.append(row)
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


def _metric_average(rows: list[EvalRow], field: str) -> float | None:
    values = [float(getattr(row.metrics, field)) for row in rows if row.metrics is not None]
    return round(sum(values) / len(values), 4) if values else None


def _quality_summary(rows: list[EvalRow], workflow_names: list[str]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    fields = (
        "verified_finding_rate",
        "supported_finding_rate",
        "eligible_finding_rate",
        "cited_source_snapshot_coverage",
        "independent_publishers",
        "blocked_sources",
        "conflicted",
    )
    for workflow in workflow_names:
        workflow_rows = [row for row in rows if row.workflow == workflow]
        values = {
            field: value
            for field in fields
            if (value := _metric_average(workflow_rows, field)) is not None
        }
        if values:
            summary[workflow] = values
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
            f"对照：{wf} 相对 {base} → 质量 {_pct(s, bs)}，token {_pct(t, bt)}，耗时 {_pct(w, bw)}"
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
        (
            "| 用例 | 流程 | Run ID | 覆盖 | 可靠 | 深度 | 可读 | 均分 | 验证率 | "
            "支持率 | 准入率 | 引用快照覆盖 | token | 耗时(s) | 预算 |"
        ),
        (
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
            "---: | ---: | ---: | ---: | ---: |"
        ),
    ]
    for r in rows:
        s = r.score
        row_budget = str(r.budget) if r.budget is not None else "不限"
        metrics = r.metrics
        verified_rate = f"{metrics.verified_finding_rate:.1%}" if metrics else "n/a"
        supported_rate = f"{metrics.supported_finding_rate:.1%}" if metrics else "n/a"
        eligible_rate = f"{metrics.eligible_finding_rate:.1%}" if metrics else "n/a"
        snapshot_coverage = f"{metrics.cited_source_snapshot_coverage:.1%}" if metrics else "n/a"
        md.append(
            f"| {r.case_id} | {r.workflow} | {r.run_id or '-'} | {s.coverage} "
            f"| {s.groundedness} | {s.depth} | {s.coherence} | {s.average} "
            f"| {verified_rate} | {supported_rate} | {eligible_rate} | {snapshot_coverage} "
            f"| {r.tokens} "
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

    deterministic = _quality_summary(rows, workflow_names)
    if deterministic:
        md += [
            "",
            "## 确定性质量指标",
            "",
            (
                "| 流程 | 逐字验证率 | 语义支持率 | 报告准入率 | 引用快照覆盖率 | "
                "平均发布方 | 平均拦截来源 | 平均冲突 |"
            ),
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for workflow, values in deterministic.items():
            md.append(
                f"| {workflow} | {values['verified_finding_rate']:.1%} "
                f"| {values['supported_finding_rate']:.1%} "
                f"| {values['eligible_finding_rate']:.1%} "
                f"| {values['cited_source_snapshot_coverage']:.1%} "
                f"| {values['independent_publishers']:.2f} "
                f"| {values['blocked_sources']:.2f} | {values['conflicted']:.2f} |"
            )

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


def benchmark_payload(
    rows: list[EvalRow],
    workflow_names: list[str],
    *,
    cases: list[EvalCase] | None = None,
    budget: int | None = None,
    run_date: str | None = None,
) -> dict[str, Any]:
    generated_at = run_date or datetime.now(UTC).isoformat()
    dataset_records = (
        [
            {
                "id": case.id,
                "query": case.query,
                "notes": case.notes,
                "category": case.category,
            }
            for case in cases
        ]
        if cases
        else []
    )
    dataset_encoded = json.dumps(
        dataset_records, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": generated_at,
        "workflows": workflow_names,
        "budget": budget,
        "dataset": [record["id"] for record in dataset_records],
        "dataset_sha256": hashlib.sha256(dataset_encoded.encode("utf-8")).hexdigest(),
        "rows": [
            {
                "case_id": row.case_id,
                "workflow": row.workflow,
                "run_id": row.run_id,
                "score": row.score.model_dump(mode="json"),
                "score_average": row.score.average,
                "tokens": row.tokens,
                "wall_seconds": round(row.wall_seconds, 3),
                "budget": row.budget,
                "manifest": row.manifest.model_dump(mode="json") if row.manifest else None,
                "metrics": row.metrics.model_dump(mode="json") if row.metrics else None,
            }
            for row in rows
        ],
        "judge_summary": {
            workflow: {
                "average_score": values[0],
                "average_tokens": values[1],
                "average_wall_seconds": values[2],
            }
            for workflow, values in _summarize(rows, workflow_names).items()
        },
        "deterministic_summary": _quality_summary(rows, workflow_names),
    }
    encoded_rows = json.dumps(payload["rows"], ensure_ascii=False, sort_keys=True)
    payload["rows_sha256"] = hashlib.sha256(encoded_rows.encode("utf-8")).hexdigest()
    return payload


def write_json_results(path: Path | str, payload: dict[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return target


def _resolve_output_path(raw: str) -> Path:
    if raw == _AUTO_OUTPUT:
        return Path(__file__).resolve().parent / "results" / f"{date.today().isoformat()}.md"
    return Path(raw)


async def _open_benchmark_repository(settings: Settings) -> tuple[SqlRepository, AsyncEngine]:
    engine = make_engine(settings.database_url)
    if settings.database_url.startswith("sqlite"):
        await prepare_sqlite_schema(engine, settings.database_url)
    return SqlRepository(make_sessionmaker(engine)), engine


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
    parser.add_argument(
        "--persist-runs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="把每个 benchmark cell 持久化并关联 run_id（默认开启）",
    )
    parser.add_argument("--baseline", type=Path, help="与历史 benchmark JSON 比较并执行回归门禁")
    parser.add_argument("--min-citation-coverage", type=float, default=0.95, metavar="RATE")
    parser.add_argument("--max-unsupported-rate", type=float, default=0.05, metavar="RATE")
    parser.add_argument("--max-conflict-rate", type=float, default=0.10, metavar="RATE")
    parser.add_argument("--max-score-drop", type=float, default=0.25, metavar="POINTS")
    parser.add_argument("--max-token-increase", type=float, default=0.25, metavar="RATE")
    args = parser.parse_args()
    workflow_names = [w.strip() for w in args.workflows.split(",") if w.strip()]
    if args.budget is not None and args.budget < 1:
        parser.error("--budget 必须 >= 1")
    rate_values = (
        args.min_citation_coverage,
        args.max_unsupported_rate,
        args.max_conflict_rate,
        args.max_token_increase,
    )
    if any(value < 0 or value > 1 for value in rate_values):
        parser.error("质量门禁 RATE 参数必须在 0~1 之间")
    if args.max_score_drop < 0:
        parser.error("--max-score-drop 必须 >= 0")

    settings = Settings()
    settings.validate()
    judge = Judge(settings)
    repository: SqlRepository | None = None
    engine: AsyncEngine | None = None
    try:
        if args.persist_runs:
            repository, engine = await _open_benchmark_repository(settings)
        budget_text = str(args.budget) if args.budget is not None else "不限"
        print(
            f"▶ 对照工作流：{', '.join(workflow_names)}（{len(CASES)} 用例，预算 {budget_text}）…"
        )
        rows = await run_comparison(
            settings,
            judge,
            CASES,
            workflow_names,
            progress=lambda case, wf: print(f"  · [{wf}] {case.id}"),
            budget=args.budget,
            repository=repository,
        )
        print("\n" + format_comparison(rows, workflow_names))
        payload = benchmark_payload(rows, workflow_names, cases=CASES, budget=args.budget)
        if args.output is not None:
            markdown = format_markdown(rows, workflow_names, cases=CASES, budget=args.budget)
            target = write_results(_resolve_output_path(args.output), markdown)
            json_target = write_json_results(target.with_suffix(".json"), payload)
            print(f"\n✔ 结果已写入 {target} 与 {json_target}")
        if args.baseline is not None:
            policy = RegressionPolicy(
                min_citation_snapshot_coverage=args.min_citation_coverage,
                max_unsupported_claim_rate=args.max_unsupported_rate,
                max_conflict_rate=args.max_conflict_rate,
                max_judge_score_drop=args.max_score_drop,
                max_token_increase_rate=args.max_token_increase,
            )
            regression = evaluate_regression(payload, load_benchmark(args.baseline), policy=policy)
            print("\n" + format_regression(regression))
            if not regression.passed:
                raise SystemExit(2)
    finally:
        await judge.aclose()
        if engine is not None:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_amain())
