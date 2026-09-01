"""语义判定校准：把「LLM 说这条论断被支持」变成一个有误差范围的数字。

## 为什么需要它

证据链上有两类判定：

* **逐字匹配**（``verification.status``）是确定性的——程序做归一化后逐字查找，
  对抗评测里的 100% 因此是可信的；
* **语义支持**（``verification.semantic_status``）由 LLM 给出，而这个判定器本身
  从未被校准过。同理 ``eval/run_eval.py`` 的四维打分也依赖 LLM judge。

也就是说，报告准入率、无支持论断率这些「确定性指标」里，有一半的输入来自一个
准确率未知的判定器。本模块给它一个数字：与人工标注的一致率、各类别的
precision/recall，以及排除了「巧合一致」的 Cohen's κ。

## 用法

```bash
# 1. 从已落库的 run 里分层抽样，导出待标注文件（human_label 留空）
python -m eval.judge_calibration export --limit 80 --output eval/calibration/semantic_cases.jsonl

# 2. 人工填写每条的 human_label（supported / unsupported / uncertain）

# 3. 出报告
python -m eval.judge_calibration report eval/calibration/semantic_cases.jsonl \
    --output eval/results/judge-calibration-2026-08-20.md
```

结论怎么用：κ 高说明 judge 可作为版本回归的度量；κ 低不代表这个项目没用，而是
说明**不应该拿 judge 分数做绝对断言**。两种结论都要如实写进 README。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from deep_research.config import Settings
from deep_research.persistence.db import make_engine, make_sessionmaker, prepare_sqlite_schema
from deep_research.persistence.sql_repository import SqlRepository

# 与 EvidenceVerification.semantic_status 对齐，去掉 not_checked：
# 没跑过语义判定的论断不进校准集。
LABELS = ("supported", "unsupported", "uncertain")


@dataclass
class CalibrationCase:
    """一条待标注/已标注的语义判定样本。"""

    run_id: str
    claim: str
    evidence_quote: str
    source_url: str
    source_excerpt: str
    judge_label: str
    judge_confidence: float = 0.0
    judge_reason: str = ""
    # 人工标注。导出时为空字符串，由标注者填写。
    human_label: str = ""

    @classmethod
    def from_json(cls, payload: dict) -> CalibrationCase:
        return cls(
            run_id=str(payload.get("run_id", "")),
            claim=str(payload.get("claim", "")),
            evidence_quote=str(payload.get("evidence_quote", "")),
            source_url=str(payload.get("source_url", "")),
            source_excerpt=str(payload.get("source_excerpt", "")),
            judge_label=str(payload.get("judge_label", "")),
            judge_confidence=float(payload.get("judge_confidence", 0.0) or 0.0),
            judge_reason=str(payload.get("judge_reason", "")),
            human_label=str(payload.get("human_label", "") or ""),
        )

    def to_json(self) -> dict:
        return {
            "run_id": self.run_id,
            "claim": self.claim,
            "evidence_quote": self.evidence_quote,
            "source_url": self.source_url,
            "source_excerpt": self.source_excerpt,
            "judge_label": self.judge_label,
            "judge_confidence": self.judge_confidence,
            "judge_reason": self.judge_reason,
            "human_label": self.human_label,
        }


@dataclass
class LabelMetrics:
    label: str
    support: int = 0  # 人工标注为该类的样本数
    predicted: int = 0  # judge 判为该类的样本数
    correct: int = 0

    @property
    def precision(self) -> float:
        return self.correct / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        return self.correct / self.support if self.support else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class CalibrationReport:
    total: int = 0
    agreements: int = 0
    kappa: float = 0.0
    confusion: dict[tuple[str, str], int] = field(default_factory=dict)
    per_label: dict[str, LabelMetrics] = field(default_factory=dict)
    skipped_unlabeled: int = 0

    @property
    def accuracy(self) -> float:
        return self.agreements / self.total if self.total else 0.0

    @property
    def verdict(self) -> str:
        """把 κ 翻译成一句能直接写进 README 的结论。

        阈值用 Landis & Koch 的惯例分档；这只是惯例，不是真理，所以措辞落在
        「这个数字允许我们做什么断言」上，而不是「judge 好不好」。
        """
        if self.total == 0:
            return "样本为空，无结论"
        if self.kappa >= 0.8:
            return "一致性很高：judge 分数可用于版本回归，也可支撑对外的相对结论"
        if self.kappa >= 0.6:
            return "一致性中等偏上：judge 分数可用于版本回归，不宜做绝对断言"
        if self.kappa >= 0.4:
            return "一致性一般：只能看趋势，单次结果需人工复核"
        return "一致性低：不应把 judge 分数当作质量证据，需先改判定 prompt 或换判定器"


def cohens_kappa(pairs: list[tuple[str, str]]) -> float:
    """Cohen's κ：扣除「碰巧一致」之后的一致度。

    直接用准确率会高估——三分类里两个随机判定器也有约 1/3 的一致率。
    κ<=0 表示不比随机猜测更好。
    """
    total = len(pairs)
    if total == 0:
        return 0.0
    observed = sum(1 for judge, human in pairs if judge == human) / total
    judge_counts = Counter(judge for judge, _ in pairs)
    human_counts = Counter(human for _, human in pairs)
    expected = sum(
        (judge_counts[label] / total) * (human_counts[label] / total)
        for label in set(judge_counts) | set(human_counts)
    )
    if expected >= 1.0:
        # 双方都把全部样本判成同一类：没有可区分的信息，κ 无定义。
        # 返回 1.0 会把「两个都只会说 supported」美化成完美一致，因此返回 0。
        return 0.0
    return (observed - expected) / (1 - expected)


def evaluate(cases: list[CalibrationCase]) -> CalibrationReport:
    labeled = [case for case in cases if case.human_label]
    report = CalibrationReport(skipped_unlabeled=len(cases) - len(labeled))
    if not labeled:
        return report

    pairs = [(case.judge_label, case.human_label) for case in labeled]
    report.total = len(pairs)
    report.agreements = sum(1 for judge, human in pairs if judge == human)
    report.kappa = cohens_kappa(pairs)

    labels = sorted({label for pair in pairs for label in pair})
    report.per_label = {label: LabelMetrics(label=label) for label in labels}
    for judge, human in pairs:
        report.confusion[(human, judge)] = report.confusion.get((human, judge), 0) + 1
        report.per_label[human].support += 1
        report.per_label[judge].predicted += 1
        if judge == human:
            report.per_label[judge].correct += 1
    return report


def format_report(report: CalibrationReport, *, source: str) -> str:
    lines = [
        "# 语义判定校准报告",
        "",
        f"样本来源：`{source}`",
        "",
        f"- 已标注样本：**{report.total}**"
        + (f"（跳过未标注 {report.skipped_unlabeled} 条）" if report.skipped_unlabeled else ""),
        f"- 与人工一致率：**{report.accuracy:.1%}**",
        f"- Cohen's κ：**{report.kappa:.3f}**",
        f"- 结论：{report.verdict}",
        "",
    ]
    if report.total == 0:
        lines.append("> 没有任何样本填写了 `human_label`，无法计算一致性。")
        return "\n".join(lines)

    labels = sorted(report.per_label)
    lines += [
        "## 混淆矩阵（行＝人工标注，列＝judge 判定）",
        "",
        "| 人工 \\ judge | " + " | ".join(labels) + " |",
        "| --- | " + " | ".join("---" for _ in labels) + " |",
    ]
    for human in labels:
        row = [str(report.confusion.get((human, judge), 0)) for judge in labels]
        lines.append(f"| {human} | " + " | ".join(row) + " |")

    lines += [
        "",
        "## 分类别指标",
        "",
        "| 类别 | 人工样本数 | judge 判定数 | precision | recall | F1 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for label in labels:
        m = report.per_label[label]
        lines.append(
            f"| {label} | {m.support} | {m.predicted} | "
            f"{m.precision:.1%} | {m.recall:.1%} | {m.f1:.1%} |"
        )
    lines += [
        "",
        "> 这些数字度量的是**语义判定器**，不是研究报告本身的正确性。",
        "> 逐字引用验证是确定性的，不在本报告范围内。",
        "",
    ]
    return "\n".join(lines)


def load_cases(path: Path) -> list[CalibrationCase]:
    cases: list[CalibrationCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(CalibrationCase.from_json(json.loads(line)))
    return cases


def write_cases(path: Path, cases: list[CalibrationCase]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(case.to_json(), ensure_ascii=False) for case in cases)
    path.write_text(payload + "\n", encoding="utf-8")


def stratified_sample(
    cases: list[CalibrationCase], limit: int, *, seed: int = 20260820
) -> list[CalibrationCase]:
    """按 judge 判定分层抽样。

    直接随机抽会得到几乎全是 ``supported`` 的样本集——那正是 judge 最容易判对的
    一类，算出来的一致率会虚高。分层保证 unsupported/uncertain 也有足够代表。
    """
    if limit <= 0 or len(cases) <= limit:
        return list(cases)
    rng = random.Random(seed)
    buckets: dict[str, list[CalibrationCase]] = {}
    for case in cases:
        buckets.setdefault(case.judge_label, []).append(case)
    for bucket in buckets.values():
        rng.shuffle(bucket)

    picked: list[CalibrationCase] = []
    # 轮转取样：小类别取完即退出轮转，剩余名额由大类别补足。
    while len(picked) < limit and any(buckets.values()):
        for label in sorted(buckets):
            bucket = buckets[label]
            if not bucket:
                continue
            picked.append(bucket.pop())
            if len(picked) >= limit:
                break
    return picked


async def collect_cases(settings: Settings, *, run_limit: int) -> list[CalibrationCase]:
    """从已落库的 run 里抽取跑过语义判定的论断。"""
    engine = make_engine(settings.database_url)
    if settings.database_url.startswith("sqlite"):
        await prepare_sqlite_schema(engine, settings.database_url)
    repo = SqlRepository(make_sessionmaker(engine))
    cases: list[CalibrationCase] = []
    try:
        for summary in await repo.list_runs(limit=run_limit):
            detail = await repo.get_run(summary.id)
            if detail is None:
                continue
            excerpts = {source.url: source.content for source in detail.sources}
            for result in detail.results:
                for finding in result.findings:
                    verification = finding.verification
                    if verification.semantic_status not in LABELS:
                        continue
                    cases.append(
                        CalibrationCase(
                            run_id=detail.id,
                            claim=finding.statement,
                            evidence_quote=finding.evidence_quote,
                            source_url=finding.source_url,
                            source_excerpt=(
                                verification.evidence_context
                                or excerpts.get(finding.source_url, "")
                            )[:2000],
                            judge_label=verification.semantic_status,
                            judge_confidence=verification.semantic_confidence,
                            judge_reason=verification.semantic_reason,
                        )
                    )
    finally:
        await engine.dispose()
    return cases


async def _export(args: argparse.Namespace) -> int:
    settings = Settings()
    cases = await collect_cases(settings, run_limit=args.run_limit)
    if not cases:
        print("没有找到任何跑过语义判定的论断——先跑几次研究再来导出。")
        return 1
    sampled = stratified_sample(cases, args.limit)
    write_cases(Path(args.output), sampled)
    spread = Counter(case.judge_label for case in sampled)
    print(f"导出 {len(sampled)} / {len(cases)} 条到 {args.output}")
    print("judge 判定分布：" + ", ".join(f"{k}={v}" for k, v in sorted(spread.items())))
    print("下一步：人工填写每行的 human_label，然后跑 `report` 子命令。")
    return 0


def _report(args: argparse.Namespace) -> int:
    path = Path(args.source)
    cases = load_cases(path)
    report = evaluate(cases)
    markdown = format_report(report, source=path.as_posix())
    print(markdown)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")
        print(f"已写入 {out}")
    return 0 if report.total else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="语义判定器与人工标注的一致性校准")
    sub = parser.add_subparsers(dest="command", required=True)

    exporter = sub.add_parser("export", help="从已落库的 run 抽样导出待标注文件")
    exporter.add_argument("--limit", type=int, default=80, help="抽样条数（默认 80）")
    exporter.add_argument("--run-limit", type=int, default=50, help="扫描最近多少次运行")
    exporter.add_argument(
        "--output",
        default="eval/calibration/semantic_cases.jsonl",
        help="导出路径",
    )

    reporter = sub.add_parser("report", help="根据已标注文件计算一致性指标")
    reporter.add_argument("source", help="已填写 human_label 的 jsonl 文件")
    reporter.add_argument("--output", default=None, help="同时写出 markdown 报告")

    args = parser.parse_args(argv)
    if args.command == "export":
        return asyncio.run(_export(args))
    return _report(args)


if __name__ == "__main__":
    raise SystemExit(main())
