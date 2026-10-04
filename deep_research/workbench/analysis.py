"""数据分析：确定性统计 + 数据图 + 只解释给定数字的中文报告。

分析分两段，边界刻意划死：

1. **计算段（纯代码，无模型）**：解析 CSV、描述统计、按分组做显著性检验
   （两组 Welch t 检验 / 多组单因素方差分析 + Kruskal–Wallis 稳健性复核）、
   数值列相关矩阵，并用 matplotlib 画图。所有数字都在这里算出，写进
   ``figure_data`` 台账——图只从台账画，图注写明统计口径。
2. **写作段（模型）**：只拿到上面算好的数字，任务是解释，不是计算。复核器会
   检查正文里的每个数字都能在统计结果里找到，否则回退为统计摘要表。

这对应「数据图的数据只准来自记录过的真实统计」这条纪律：模型从来不接触原始
数据，也就不可能在报告里写出一个没算过的均值。
"""

from __future__ import annotations

import hashlib
import io
import math
import re
from dataclasses import dataclass, field
from typing import Any

from ..agents.base import Blackboard, RunContext
from ..models import Report
from ..persistence.repository import LeaseLostError
from ..registry import register
from .contract import TaskContract, contract_from_scratch
from .templates import get_template
from .writers import _BASE_SYSTEM, WORKBENCH_SCRATCH_KEY, WriterState, _skeleton

ANALYSIS_SCRATCH_KEY = "analysis"
MAX_ROWS = 50_000
MAX_COLUMNS = 60


class DatasetError(ValueError):
    """数据无法解析或不适合分析。"""


@dataclass
class Figure:
    name: str
    title: str
    caption: str
    png: bytes


@dataclass
class AnalysisResult:
    question: str
    rows: int
    columns: list[str]
    numeric: list[str]
    categorical: list[str]
    missing: dict[str, int]
    describe: list[dict[str, Any]]
    tests: list[dict[str, Any]]
    correlations: list[dict[str, Any]]
    figures: list[Figure] = field(default_factory=list)
    synthetic: bool = False
    # 数据来源（文件名 / 工作表），由任务契约提供；粘贴的表格为空
    source: dict[str, Any] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    input_sha256: str = ""
    figure_policy: int = 2
    scope: dict[str, Any] | None = None
    composition: list[dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        """Freeze computed values; plots can be redrawn from these and the same input."""
        return {
            "version": 5 if self.scope is not None else 4,
            "facts": self.facts(),
            **(
                {"scope": self.scope, "composition": self.composition}
                if self.scope is not None
                else {}
            ),
            **{
                name: getattr(self, name)
                for name in (
                    "rows",
                    "columns",
                    "numeric",
                    "categorical",
                    "missing",
                    "describe",
                    "tests",
                    "correlations",
                    "synthetic",
                    "source",
                    "issues",
                    "input_sha256",
                    "figure_policy",
                )
            },
            "figures": [
                {"name": f.name, "title": f.title, "caption": f.caption} for f in self.figures
            ],
        }

    def source_label(self) -> str:
        if self.synthetic:
            return "合成示例数据"
        where = str(self.source.get("filename") or "粘贴的表格")
        if self.source.get("sheet"):
            where += f"，工作表「{self.source['sheet']}」"
        return where

    def facts(self) -> str:
        """给写作段的数字台账（Markdown）。"""
        lines = [
            f"- 数据来源：{self.source_label()}",
            f"- 样本量：{self.rows} 行，{len(self.columns)} 列",
            f"- 全部列名：{', '.join(self.columns)}",
            f"- 数值变量：{', '.join(self.numeric) or '无'}",
            f"- 分类变量：{', '.join(self.categorical) or '无'}",
            "- 未纳入上述统计分组的列："
            + (
                ", ".join(
                    column
                    for column in self.columns
                    if column not in self.numeric + self.categorical
                )
                or "无"
            ),
        ]
        if self.synthetic:
            lines.append("- 注意：用户未提供数据，以下为演示用合成数据")
        missing = {k: v for k, v in self.missing.items() if v}
        lines.append(f"- 缺失值：{missing or '无'}")
        if self.scope is not None:
            lines.append("- 本次统计仅覆盖所选测量与比较分组，其他列仍完整保留在输入数据中。")
            lines.append("- 仅作样本背景的列：" + (", ".join(self.scope["background"]) or "无"))
            if self.composition:
                lines.append("\n### 分组与背景构成（每列分别计数，不代表每项测量的有效样本量）")
                for item in self.composition:
                    lines.append(
                        f"- {item['column']}: 有效 n={item['n']}，缺失={item['missing']}，"
                        f"不同取值数={item['distinct']}；构成="
                        + ", ".join(f"{level['label']}: n={level['n']}" for level in item["levels"])
                    )
        if self.tests or self.correlations:
            lines.append("- 多重比较校正：本次统计未执行，台账 p 值为未经校正的结果。")
        lines.append("\n### 描述统计")
        for row in self.describe:
            lines.append(
                f"- {row['variable']}: n={row['n']}, 均值={row['mean']}, 标准差={row['std']}, "
                f"中位数={row['median']}, 最小={row['min']}, 最大={row['max']}"
            )
        if self.tests:
            lines.append("\n### 显著性检验")
            for test in self.tests:
                verdict = (
                    ("显著" if test["significant"] else "不显著")
                    if test["significant"] is not None
                    else "无法检验"
                )
                lines.append(
                    f"- {test['variable']} ~ {test['group']}（{test['method']}）："
                    f"统计量={test['statistic']}, p={test['p_value']}, "
                    f"{verdict}（α=0.05）"
                    + (
                        f"；稳健性复核 {test['robust_method']} p={test['robust_p']}"
                        if test.get("robust_method")
                        else ""
                    )
                )
                if test.get("paired"):
                    lines.append(
                        f"  差值方向：{test['right']} − {test['left']}；"
                        f"完整配对数={test['n_pairs']}，"
                        f"排除不完整配对={test['excluded_pairs']}，平均差={test['mean_difference']}，"
                        f"差值标准差={test['difference_std']}，自由度={test['df']}，"
                        f"95% 均值差置信区间=[{test['ci_low']}, {test['ci_high']}]"
                    )
                    lines.append(
                        "  推断前提：各配对对象独立，差值满足配对 t 检验的分布假设；"
                        "本轮未自动验证这些前提。"
                    )
                for summary in test.get("group_summaries", []):
                    lines.append(
                        f"  分组 {summary['label']}: n={summary['n']}, 均值={summary['mean']}, "
                        f"标准差={summary['std']}, 中位数={summary['median']}"
                    )
                if test.get("eta_squared") is not None:
                    lines.append(
                        f"  样本效应量 η²={test['eta_squared']}（组间平方和/总平方和），"
                        f"有效总样本量={test['n_total']}。该量描述样本关联，不作因果解释。"
                    )
                if test.get("df_between") is not None:
                    lines.append(
                        f"  方差分析自由度：组间={test['df_between']}，组内={test['df_within']}。"
                    )
                if test.get("method") == "单因素方差分析":
                    lines.append(
                        "  推断前提：观测独立、各组残差近似正态且方差齐；"
                        "本轮未自动验证这些前提，非参数复核不等于前提已成立。"
                    )
                elif test.get("method") == "Welch t 检验":
                    lines.append(
                        "  推断前提：两组独立、均值推断的分布条件适用；"
                        "不要求两组方差相等，本轮未自动验证独立性或分布前提。"
                    )
                if test.get("reason"):
                    lines.append(f"  检验限制：{test['reason']}")
        if self.correlations:
            lines.append("\n### 相关性（Pearson）")
            for item in self.correlations:
                lines.append(
                    f"- {item['a']} 与 {item['b']}：r={item['r']}, p={item['p_value']}"
                    + (f"，有效样本量 n={item['n']}" if "n" in item else "")
                )
        if self.figures:
            lines.append("\n### 图表")
            lines += [f"- {fig.title}：{fig.caption}" for fig in self.figures]
        if self.issues:
            lines.append("\n### 分析尚未完成的项目")
            lines.extend(f"- {issue}" for issue in self.issues)
        return "\n".join(lines)

    def summary_table(self) -> list[dict[str, Any]]:
        return self.describe


def ledger_facts(snapshot: dict[str, Any]) -> str:
    """Reuse the writer's exact frozen facts; project old snapshots without recalculating."""
    if isinstance(snapshot.get("facts"), str):
        return snapshot["facts"]
    required = {
        "rows",
        "columns",
        "numeric",
        "categorical",
        "missing",
        "describe",
        "tests",
        "correlations",
    }
    if not required.issubset(snapshot):
        import json

        known = {key: value for key, value in snapshot.items() if key != "synthetic"}
        return (
            "历史统计记录仅保留以下字段，未补算缺失值、列角色或检验前提；"
            "未保留的信息不可视为不存在：\n" + json.dumps(known, ensure_ascii=False)
        )
    fields = {
        key: snapshot[key]
        for key in (
            "rows",
            "columns",
            "numeric",
            "categorical",
            "missing",
            "describe",
            "tests",
            "correlations",
        )
    }
    result = AnalysisResult(
        question="",
        **fields,
        synthetic=bool(snapshot.get("synthetic")),
        source=dict(snapshot.get("source", {})),
        issues=list(snapshot.get("issues", [])),
        scope=snapshot.get("scope"),
        composition=list(snapshot.get("composition", [])),
    )
    result.figures = [
        Figure(name=f["name"], title=f["title"], caption=f["caption"], png=b"")
        for f in snapshot.get("figures", [])
    ]
    return result.facts()


def _fmt(value: float) -> float | str:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return "NA"
    magnitude = abs(value)
    if magnitude != 0 and (magnitude < 1e-3 or magnitude >= 1e6):
        return float(f"{value:.3e}")
    return round(float(value), 4)


def _synthetic_csv() -> str:
    import numpy as np

    rng = np.random.default_rng(20260926)
    rows = ["method,scene,psnr"]
    for method, mu in (("A", 32.0), ("B", 33.5), ("C", 34.2)):
        for scene in range(1, 11):
            rows.append(f"{method},{scene},{rng.normal(mu, 0.8):.2f}")
    return "\n".join(rows)


def parse_dataset(csv_text: str) -> Any:
    import pandas as pd

    text = csv_text.strip()
    if not text:
        raise DatasetError("没有可分析的数据")
    delimiter = "\t" if text.splitlines()[0].count("\t") > text.splitlines()[0].count(",") else ","
    try:
        frame = pd.read_csv(io.StringIO(text), sep=delimiter, nrows=MAX_ROWS + 1)
    except Exception as exc:
        raise DatasetError(f"CSV 解析失败：{exc}") from exc
    if len(frame) > MAX_ROWS:
        raise DatasetError(f"数据超过 {MAX_ROWS} 行上限")
    if frame.shape[1] > MAX_COLUMNS:
        raise DatasetError(f"数据超过 {MAX_COLUMNS} 列上限")
    if frame.empty or frame.shape[1] < 1:
        raise DatasetError("数据为空")
    nonfinite = frame.select_dtypes(include="number").isin([float("inf"), float("-inf")]).any()
    if nonfinite.any():
        raise DatasetError(
            "数值列含无穷值，请先清理：" + ", ".join(str(c) for c in nonfinite.index[nonfinite])
        )
    frame.columns = [str(column).strip() or f"col{i}" for i, column in enumerate(frame.columns)]
    return frame


def _chart_font() -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager, rcParams

    available = {font.name for font in font_manager.fontManager.ttflist}
    for name in (
        "Microsoft YaHei",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "SimHei",
        "WenQuanYi Zen Hei",
    ):
        if name in available:
            rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            break
    rcParams["axes.unicode_minus"] = False


def _png(fig: Any) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=160, bbox_inches="tight")
    import matplotlib.pyplot as plt

    plt.close(fig)
    return buffer.getvalue()


def _png_cached(fig: Any, name: str, cache: dict[str, bytes] | None) -> bytes:
    if cache is not None and name in cache:
        import matplotlib.pyplot as plt

        plt.close(fig)
        return cache[name]
    return _png(fig)


def analyse(
    csv_text: str,
    question: str = "",
    *,
    allow_synthetic: bool = False,
    source: dict[str, Any] | None = None,
    frozen: dict[str, Any] | None = None,
    scope: dict[str, Any] | None = None,
    png_cache: dict[str, bytes] | None = None,
    on_figure: Any = None,
) -> AnalysisResult:
    """对一张表做确定性分析。

    没有数据时只在 ``allow_synthetic``（用户主动选择演示）下改用可复现的合成示例，
    并如实标注；否则抛 ``DatasetError``，不拿示例数据冒充用户的数据。
    """
    import numpy as np
    import pandas as pd
    from scipy import stats

    synthetic = not csv_text.strip()
    if synthetic and not allow_synthetic:
        raise DatasetError("没有可分析的数据：请上传 CSV / TSV / XLSX 表格或粘贴数据")
    input_text = _synthetic_csv() if synthetic else csv_text
    input_sha256 = hashlib.sha256(input_text.strip().encode()).hexdigest()
    if frozen and frozen.get("input_sha256") and frozen["input_sha256"] != input_sha256:
        raise DatasetError("输入数据与任务统计快照不一致，需要重新执行分析，不能与旧报告混用")
    frame = parse_dataset(input_text)
    issues: list[str] = []
    numeric = [
        c
        for c in frame.columns
        if pd.api.types.is_numeric_dtype(frame[c]) and not _looks_like_identifier(frame[c])
    ]
    categorical = [
        c
        for c in frame.columns
        if c not in numeric
        and not pd.api.types.is_numeric_dtype(frame[c])
        and 1 < frame[c].nunique(dropna=True) <= min(20, max(2, len(frame) // 2))
    ]
    if frozen is not None:
        if scope is not None and scope != frozen.get("scope"):
            raise DatasetError("分析范围与冻结统计不一致，不能混用")
        scope = frozen.get("scope")
    composition = []
    if scope is not None:
        from .analysis_scope import AnalysisScope, background_summary, validate_scope

        selected = AnalysisScope.model_validate(scope)
        validate_scope(frame, selected)
        numeric, categorical = list(selected.measures), list(selected.groups)
        composition = background_summary(frame, selected.groups + selected.background)
        scope = selected.model_dump(mode="json")
    describe: list[dict[str, Any]] = []
    for column in numeric:
        series = frame[column].dropna()
        if series.empty:
            continue
        describe.append(
            {
                "variable": column,
                "n": int(series.count()),
                "mean": _fmt(series.mean()),
                "std": _fmt(series.std(ddof=1)) if len(series) > 1 else "NA",
                "median": _fmt(series.median()),
                "min": _fmt(series.min()),
                "max": _fmt(series.max()),
            }
        )

    paired_requested = bool(
        re.search(r"配对|成对|\bpaired\b|同一[组批].*(?:场景|样本|对象)", question, re.I)
        and not re.search(r"非配对|不配对|不要配对|无需配对|不做配对|\bunpaired\b", question, re.I)
    )
    tests: list[dict[str, Any]] = []
    for group in [] if paired_requested else categorical:
        for variable in numeric:
            grouped = [
                (str(label), values.dropna().to_numpy())
                for label, values in frame.groupby(group, dropna=True)[variable]
            ]
            samples = [values for _, values in grouped]
            if len(samples) < 2 or any(len(sample) < 2 for sample in samples):
                issues.append(
                    f"{variable} 按 {group} 的检验未执行：至少两组，且每组至少两条有效观测"
                )
                continue
            method = "Welch t 检验" if len(samples) == 2 else "单因素方差分析"
            try:
                if len(samples) == 2:
                    result = stats.ttest_ind(samples[0], samples[1], equal_var=False)
                    robust = stats.mannwhitneyu(samples[0], samples[1])
                    robust_name = "Mann–Whitney U"
                else:
                    result = stats.f_oneway(*samples)
                    robust = stats.kruskal(*samples)
                    robust_name = "Kruskal–Wallis"
                if not all(
                    math.isfinite(float(value))
                    for value in (result.statistic, result.pvalue, robust.pvalue)
                ):
                    raise ValueError("退化样本导致非有限检验结果")
            except ValueError:
                reason = f"{variable} 按 {group} 的检验无法估计：样本无有效变异或不满足检验条件"
                issues.append(reason)
                tests.append(
                    {
                        "variable": variable,
                        "group": group,
                        "method": method,
                        "statistic": "NA",
                        "p_value": "NA",
                        "significant": None,
                        "groups": len(samples),
                        "reason": reason,
                    }
                )
                continue
            p_value = float(result.pvalue)
            n_total = sum(len(sample) for sample in samples)
            overall_mean = sum(float(np.sum(sample)) for sample in samples) / n_total
            total_ss = sum(float(np.sum((sample - overall_mean) ** 2)) for sample in samples)
            between_ss = sum(
                len(sample) * (float(np.mean(sample)) - overall_mean) ** 2 for sample in samples
            )
            eta_squared = (
                between_ss / total_ss
                if total_ss > 0 and math.isfinite(total_ss) and math.isfinite(between_ss)
                else None
            )
            tests.append(
                {
                    "variable": variable,
                    "group": group,
                    "method": method,
                    "statistic": _fmt(float(result.statistic)),
                    "p_value": _fmt(p_value),
                    "significant": bool(p_value < 0.05),
                    "robust_method": robust_name,
                    "robust_p": _fmt(float(robust.pvalue)),
                    "groups": int(len(samples)),
                    "n_total": n_total,
                    "df_between": len(samples) - 1 if len(samples) > 2 else None,
                    "df_within": n_total - len(samples) if len(samples) > 2 else None,
                    "eta_squared": _fmt(eta_squared) if eta_squared is not None else None,
                    "group_summaries": [
                        {
                            "label": label,
                            "n": len(sample),
                            "mean": _fmt(float(np.mean(sample))),
                            "std": _fmt(float(np.std(sample, ddof=1))),
                            "median": _fmt(float(np.median(sample))),
                        }
                        for label, sample in grouped
                    ],
                }
            )

    if paired_requested:
        if len(numeric) != 2:
            issues.append(
                "配对分析目前支持两列宽表，每行一对；当前配对列不明确，"
                "需先选择测量列或转换长表，未改用独立样本检验"
            )
        else:
            left, right = numeric
            pairs = frame[[left, right]].dropna()
            difference = pairs[right] - pairs[left]
            n = len(pairs)
            mean = float(difference.mean())
            std = float(difference.std(ddof=1)) if n >= 2 else math.nan
            statistic = pvalue = lower = upper = math.nan
            reason = ""
            if n < 2 or not math.isfinite(std) or std <= 0:
                reason = "完整配对不足或差值无有效变异，无法估计配对 t 检验及置信区间"
                issues.append(reason)
            else:
                result = stats.ttest_rel(pairs[right], pairs[left])
                statistic, pvalue = float(result.statistic), float(result.pvalue)
                margin = float(stats.t.ppf(0.975, n - 1)) * std / math.sqrt(n)
                lower, upper = mean - margin, mean + margin
                if not all(math.isfinite(value) for value in (statistic, pvalue, lower, upper)):
                    statistic = pvalue = lower = upper = math.nan
                    reason = "数值不稳定，无法可靠估计配对 t 检验及置信区间"
                    issues.append(reason)
            tests.append(
                {
                    "paired": True,
                    "left": left,
                    "right": right,
                    "variable": f"{right} − {left}",
                    "group": "同一行配对",
                    "method": "配对 t 检验",
                    "statistic": _fmt(statistic),
                    "p_value": _fmt(pvalue),
                    "significant": bool(pvalue < 0.05) if math.isfinite(pvalue) else None,
                    "n_pairs": n,
                    "excluded_pairs": len(frame) - n,
                    "groups": 2,
                    "mean_difference": _fmt(mean),
                    "difference_std": _fmt(std),
                    "df": n - 1 if n else "NA",
                    "ci_low": _fmt(lower),
                    "ci_high": _fmt(upper),
                    "reason": reason,
                }
            )

    correlations: list[dict[str, Any]] = []
    for i, left in enumerate(numeric):
        for right in numeric[i + 1 :]:
            pair = frame[[left, right]].dropna()
            if len(pair) < 3 or pair[left].nunique() < 2 or pair[right].nunique() < 2:
                continue
            r, p = stats.pearsonr(pair[left], pair[right])
            correlations.append(
                {
                    "a": left,
                    "b": right,
                    "r": _fmt(float(r)),
                    "p_value": _fmt(float(p)),
                    "n": len(pair),
                }
            )

    # Downloads must use the ledger that produced the report. In particular,
    # upgrading the analysis code must not add new tests to an old narrative.
    if frozen and all(
        isinstance(frozen.get(key), list)
        for key in ("describe", "tests", "correlations", "columns")
    ):
        if list(frozen["columns"]) != list(frame.columns) or frozen.get("rows") != len(frame):
            raise DatasetError("数据表结构与统计快照不一致，不能与旧报告混用")
        describe, tests, correlations = frozen["describe"], frozen["tests"], frozen["correlations"]
        numeric = frozen.get("numeric", [row["variable"] for row in describe])
        categorical = frozen.get("categorical", categorical)
        issues = list(frozen.get("issues", []))
        if scope is not None:
            if numeric != scope["measures"] or categorical != scope["groups"]:
                raise DatasetError("冻结统计的变量与分析范围不一致，不能混用")
            composition = list(frozen.get("composition", []))

    _chart_font()
    import matplotlib.pyplot as plt

    figures: list[Figure] = []

    def add_figure(figure: Figure) -> None:
        figures.append(figure)
        if on_figure is not None:
            on_figure(figure)

    # Old reports refer to specific plots. Preserve their recorded policy when
    # redrawing a download; expanded coverage applies to new analyses only.
    figure_policy = int(frozen.get("figure_policy", 1)) if frozen else 2
    if figure_policy >= 2 and (not paired_requested or not tests):
        from .analysis_figures import distribution_figures

        figures.extend(
            distribution_figures(
                frame,
                numeric,
                [] if paired_requested else categorical,
                png_cache=png_cache,
                on_figure=on_figure,
            )
        )
    legacy_tests = (
        [item for item in tests if not item.get("paired")][:3] if figure_policy == 1 else []
    )
    for test in legacy_tests:
        fig, ax = plt.subplots(figsize=(6.4, 3.8))
        grouped = frame.groupby(test["group"])[test["variable"]]
        labels = [str(label) for label, _ in grouped]
        ax.boxplot([values.dropna().to_numpy() for _, values in grouped], tick_labels=labels)
        ax.set_title(f"{test['variable']} 按 {test['group']} 分组")
        ax.set_xlabel(test["group"])
        ax.set_ylabel(test["variable"])
        ax.grid(axis="y", alpha=0.3)
        name = (
            f"fig_{len(figures) + 1:02d}_box_{_safe(test['variable'])}_{_safe(test['group'])}.png"
        )
        add_figure(
            Figure(
                name=name,
                title=f"{test['variable']} 按 {test['group']} 分组箱线图",
                caption=f"统计口径：每组有效样本；{test['method']} p={test['p_value']}",
                png=_png_cached(fig, name, png_cache),
            )
        )
    for test in [item for item in tests if item.get("paired")]:
        pairs = frame[[test["left"], test["right"]]].dropna()
        if pairs.empty:
            continue
        fig, ax = plt.subplots(figsize=(6.4, 3.8))
        ax.boxplot(
            [pairs[test["left"]], pairs[test["right"]]], tick_labels=[test["left"], test["right"]]
        )
        shown = min(len(pairs), 100)
        for _, row in pairs.iloc[np.linspace(0, len(pairs) - 1, shown, dtype=int)].iterrows():
            ax.plot(
                [1, 2],
                [row[test["left"]], row[test["right"]]],
                color="#547c95",
                alpha=0.3,
                linewidth=0.6,
            )
        ax.set_title("测量分布与逐对连线")
        ax.set_ylabel("测量值（单位同输入）")
        ax.grid(axis="y", alpha=0.3)
        name = f"fig_{len(figures) + 1:02d}_paired.png"
        add_figure(
            Figure(
                name=name,
                title="测量分布与逐对连线",
                caption=(
                    f"同一行配对；n={test['n_pairs']}；差值方向 {test['right']} − {test['left']}；"
                    f"配对 t 检验 p={test['p_value']}。箱线图用全部完整配对，连线展示 {shown} 对；"
                    "纵轴单位同输入测量值，不表示差值分布"
                ),
                png=_png_cached(fig, name, png_cache),
            )
        )
    if figure_policy == 1 and not tests and numeric:
        column = numeric[0]
        fig, ax = plt.subplots(figsize=(6.4, 3.8))
        ax.hist(frame[column].dropna().to_numpy(), bins=min(20, max(5, len(frame) // 5)))
        ax.set_title(f"{column} 分布")
        ax.set_xlabel(column)
        ax.set_ylabel("频数")
        name = f"fig_01_hist_{_safe(column)}.png"
        add_figure(
            Figure(
                name=name,
                title=f"{column} 分布直方图",
                caption=f"统计口径：{column} 的全部非缺失值（n={int(frame[column].count())}）",
                png=_png_cached(fig, name, png_cache),
            )
        )
    if len(numeric) >= 2:
        matrix = frame[numeric].corr(numeric_only=True).to_numpy()
        fig, ax = plt.subplots(figsize=(5.2, 4.4))
        image = ax.imshow(matrix, vmin=-1, vmax=1, cmap="RdBu_r")
        ax.set_xticks(range(len(numeric)), numeric, rotation=45, ha="right")
        ax.set_yticks(range(len(numeric)), numeric)
        for (row, col), value in np.ndenumerate(matrix):
            ax.text(col, row, f"{value:.2f}", ha="center", va="center", fontsize=8)
        fig.colorbar(image, ax=ax, shrink=0.8)
        ax.set_title("数值变量 Pearson 相关矩阵")
        name = f"fig_{len(figures) + 1:02d}_correlation.png"
        add_figure(
            Figure(
                name=name,
                title="数值变量相关矩阵",
                caption="两两成对删除缺失值后的 Pearson r；色标与格内数值均无量纲，不是测量单位",
                png=_png_cached(fig, name, png_cache),
            )
        )

    return AnalysisResult(
        question=question,
        rows=int(len(frame)),
        columns=list(frame.columns),
        numeric=numeric,
        categorical=categorical,
        missing={c: int(frame[c].isna().sum()) for c in frame.columns},
        describe=describe,
        tests=tests,
        correlations=correlations,
        figures=figures,
        synthetic=synthetic,
        source={} if synthetic else dict(source or {}),
        issues=issues,
        input_sha256=input_sha256,
        figure_policy=figure_policy,
        scope=scope,
        composition=composition,
    )


def allows_synthetic(contract: TaskContract | None) -> bool:
    """用户主动选了示例演示才用合成数据；契约早于该字段的旧任务保持原行为。"""
    return contract is None or contract.demo_data is not False


def _looks_like_identifier(series: Any) -> bool:
    """Use explicit identifier names; integer-valued measurements remain measurements."""
    name = str(getattr(series, "name", "")).strip().casefold()
    return (
        name in {"id", "index", "idx", "scene", "subject", "participant", "sample", "trial"}
        or name.endswith("_id")
        or any(word in name for word in ("编号", "序号"))
    )


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:24] or "var"


def _numbers(text: str) -> set[str]:
    return {
        m.rstrip(".").lower()
        for m in re.findall(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text)
    }


def check_numbers(body: str, facts: str, *, input_description: str = "") -> list[str]:
    """正文里出现、却不在统计台账里的数字（排除章节序号与 α=0.05 这类常量）。"""
    allowed = _numbers(facts) | {"0.05", "0", "1", "2", "3", "4", "5", "10", "95", "100"}
    stripped = re.sub(r"(?m)^\s*#+.*$|^\s*\d+[.)、]\s", "", body)
    row_reference = re.compile(r"第\s*(\d+)\s*(?:行|条(?:记录|数据|样本)?|个(?:样本|记录))")
    declared_rows = {match[1] for match in row_reference.finditer(input_description)}
    # A declared record number identifies input provenance; it cannot justify
    # a statistical value with the same digits elsewhere in the paragraph.
    stripped = row_reference.sub(
        lambda match: "输入记录" if match[1] in declared_rows else match[0], stripped
    )
    return sorted(n for n in _numbers(stripped) if n not in allowed)


def fallback_report(result: AnalysisResult) -> str:
    """确定性的统计报告：模型解释不可用或不可信时的交付正文。

    结论段同样由代码写出——逐条陈述每项检验是否显著，数字全部取自台账，
    这样即使没有模型，交付物也是一份完整、可读的分析报告。
    """
    facts = result.facts()
    lines = ["## 分析计划", f"问题：{result.question or '对数据做探索性分析'}。"]
    methods = ["描述统计"]
    if result.tests:
        methods.extend(
            dict.fromkeys(
                str(test["method"]) for test in result.tests if test.get("statistic") is not None
            )
        )
        methods.extend(
            dict.fromkeys(
                str(test["robust_method"])
                for test in result.tests
                if test.get("robust_method") and test.get("robust_p") is not None
            )
        )
    if result.correlations:
        methods.append("数值变量两两 Pearson 相关")
    lines.append("方法：" + "；".join(methods) + "。")
    lines += ["", "## 数据概况", facts.split("\n### 描述统计")[0]]
    lines += [
        "",
        "## 统计结果",
        "### 描述统计" + facts.split("### 描述统计", 1)[-1].split("\n### 图表")[0],
    ]
    lines += ["", "## 图表"] + [f"- {fig.title}：{fig.caption}" for fig in result.figures]
    conclusions = []
    for test in result.tests:
        verdict = (
            "存在显著差异"
            if test["significant"] is True
            else "未发现显著差异"
            if test["significant"] is False
            else "未完成差异检验"
        )
        conclusions.append(
            f"- {test['variable']} 在不同 {test['group']} 之间{verdict}"
            f"（{test['method']}，p={test['p_value']}"
            + (
                f"；{test['robust_method']} p={test['robust_p']}"
                if test.get("robust_method") and test.get("robust_p") is not None
                else ""
            )
            + "）。"
        )
    for item in result.correlations:
        conclusions.append(
            f"- {item['a']} 与 {item['b']} 的 Pearson r={item['r']}（p={item['p_value']}）。"
        )
    if not conclusions:
        conclusions.append("- 数据中没有可检验的分组或相关关系，仅给出描述统计。")
    conclusions.append("- 以上结论依据本次实际执行的统计；检验前提与适用范围见台账及分析说明。")
    lines += ["", "## 结论", *conclusions]
    return "\n".join(lines)


@register("data_analyst")
class DataAnalyst:
    """执行确定性统计分析，再让模型只解释算好的数字。"""

    name: str

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        from ..blocking import run_blocking

        template = get_template("dataAnalysis")
        assert template is not None
        contract = contract_from_scratch(bb.scratch)
        question = contract.focus if contract is not None else bb.query
        csv_text = contract.dataset_csv if contract is not None else ""
        from .quality import coerce_policy
        from .writing_progress import (
            WritingProgressError,
            finish,
            for_writer,
            restore_finished,
            restore_prose,
        )

        policy = coerce_policy(contract.quality if contract is not None else ctx.settings.quality)
        progress = for_writer(
            bb,
            ctx,
            self.name,
            _BASE_SYSTEM + _skeleton(template),
            inputs={
                "csv": csv_text,
                "question": question,
                "analysis": bb.scratch.get(ANALYSIS_SCRATCH_KEY),
                "scope": bb.scratch.get("analysis_scope"),
            },
        )
        if await restore_finished(progress, bb, policy.max_revisions):
            return bb
        writing_state = await progress.load(policy.max_revisions) if progress else None
        if writing_state:
            if not set(writing_state.preparation) <= {ANALYSIS_SCRATCH_KEY, "analysis_scope"}:
                raise WritingProgressError("统计准备进度包含未知字段")
            bb.scratch.update(writing_state.preparation)
        ctx.tracer.emit("RESEARCHER", "start", "解析数据并执行统计分析…")
        try:
            from .analysis_scope import plan_scope

            frozen = bb.scratch.get(ANALYSIS_SCRATCH_KEY)
            selected_scope = None
            if csv_text.strip() and not isinstance(frozen, dict):
                ctx.tracer.emit("RESEARCHER", "info", "根据问题确定测量变量、比较分组与背景字段…")
                selected_scope = (
                    await plan_scope(ctx.llm_for(self.name), csv_text, question, bb.scratch)
                ).model_dump(mode="json")
                if progress and writing_state:
                    writing_state.preparation["analysis_scope"] = bb.scratch["analysis_scope"]
                    await progress.save(writing_state)
            result = await run_blocking(
                analyse,
                csv_text,
                question,
                allow_synthetic=allows_synthetic(contract),
                source=contract.dataset_source if contract is not None else None,
                scope=selected_scope,
                frozen=frozen if isinstance(frozen, dict) else None,
            )
            if progress and writing_state:
                writing_state.preparation[ANALYSIS_SCRATCH_KEY] = result.snapshot()
                await progress.save(writing_state)
        except DatasetError as exc:
            bb.report = Report(query=bb.query, markdown=f"## 分析计划\n\n数据无法分析：{exc}\n")
            bb.scratch[WORKBENCH_SCRATCH_KEY] = WriterState(
                template=template.key, extras={"error": str(exc)}
            ).model_dump(mode="json")
            return bb
        facts = result.facts()
        ctx.tracer.emit(
            "RESEARCHER",
            "info",
            f"统计完成：{result.rows} 行，{len(result.tests)} 项检验，{len(result.figures)} 张图",
            data={"category": "analysis", "rows": result.rows, "tests": len(result.tests)},
        )
        system = ctx.system_prompt(
            _BASE_SYSTEM.replace("引用事实时保留素材的 [n] 角标，", "")
            + "\n\n"
            + _skeleton(template)
            + "\n\n你只负责解释下面【统计台账】里的数字，不得计算、改写或编造任何数字；"
            "引用数字时原样照抄台账写法。用中文写作。"
            "相关分析不能替代配对差异、显著性或一致性检验。"
            "未执行的检验只能标注本轮未执行，不要将其说成用户没有提供原始数据。"
            "均值与中位数接近不能证明分布对称、无偏或满足检验前提。"
            "区分各方法的标准差、配对差值标准差与方法间均值差，不把它们统称为残余或总体离散。"
            "不要在报告中声明‘所有数字照抄、未经改写’等写作过程保证，直接陈述统计事实。"
            "用户输入说明中的行号可作为输入来源说明复述，不能当作测量值或统计量。"
            "效应量按台账数值及样本含义解释，不引用台账未提供的经验阈值作大小分级。"
            "总体有效样本量、单个分组样本量和变量对样本量必须分别陈述，"
            "‘各组、均、其余及其分组’等概括不能把总样本量分配给每一组。"
            "分组与背景构成按该列本身计数，不能替代删除测量缺失值后的分组有效样本量。"
            "明确区分所选测量、比较分组和仅作背景的列；未参与检验不表示原始数据未提供。"
        )
        user = f"分析问题：{question or '对数据做探索性分析'}\n\n## 统计台账\n{facts}\n"
        from .tables import TABLES_KEY, render_specs, review_tables, table_preview

        system += (
            "表格只能用 evidence-table 代码块写 JSON 规格："
            '{"id":"t1","title":"表 1 描述统计","ledger_path":["describe"]}。'
            "ledger_path 指向统计台账中的记录数组，代码保留其原始字段名并取值；"
            "不直接写 Markdown、HTML 或 LaTeX 表格。可用路径包括 describe、tests、correlations，"
            "以及 tests 下的数字序号、group_summaries。"
        )
        table_records: dict[str, dict[str, Any]] = {}
        from .gates import structure_gate
        from .prose_review import PROSE_REVIEW_KEY, reviewer_for_report
        from .revision import Assessment, write_with_revisions
        from .scholarly import check_register

        review_scratch = {
            **bb.scratch,
            "workbench": {"template": "dataAnalysis"},
            "analysis": result.snapshot(),
        }
        reviewer = reviewer_for_report(
            ctx.llm_for("evidence_verifier"),
            bb.query,
            [],
            [],
            review_scratch,
            ctx.settings.llm_max_input_chars,
        )
        assert reviewer is not None
        current_body = ""

        from .content_revision import revision_seed

        seed, seed_review = revision_seed(bb)
        seed_pending = seed is not None

        async def write(revision: str | None) -> str:
            nonlocal seed_pending, current_body
            if seed_pending and seed is not None:
                seed_pending = False
                reviewer.prime(seed.markdown, seed_review)
                initial = await assess(seed.markdown)
                if initial.clean or not initial.can_revise:
                    current_body = seed.markdown
                    table_records.setdefault(current_body, bb.scratch.get(TABLES_KEY) or {})
                    return seed.markdown
                from .revision import revision_prompt

                revision = revision_prompt(seed.markdown, initial)
            chunks: list[str] = []
            first = True
            async for delta in ctx.llm_for(self.name).stream(
                system, user + (revision or ""), temperature=0.2
            ):
                chunks.append(delta)
                raw = "".join(chunks)
                has_specs = "evidence-table" in raw
                ctx.tracer.emit(
                    "SYNTHESIZER",
                    "token",
                    data={
                        "delta": table_preview(raw) if has_specs else delta,
                        **({"replace": True} if has_specs or (first and revision) else {}),
                    },
                )
                first = False
            body, table_record = render_specs(
                "".join(chunks).strip(),
                [],
                {},
                ledger=result.snapshot(),
                previous=bb.scratch.get(TABLES_KEY),
            )
            table_records[body] = table_record
            bb.scratch[TABLES_KEY] = table_record
            if body != "".join(chunks).strip():
                ctx.tracer.emit("SYNTHESIZER", "token", data={"delta": body, "replace": True})
            current_body = body
            return body

        async def assess(draft: str) -> Assessment:
            nonlocal current_body
            current_body = draft
            # 数字与章节是硬性要求；文体问题同样要求修订。三者都是确定性检查。
            hard = [
                f"数字「{n}」不在统计台账中"
                for n in check_numbers(draft, facts, input_description=question)[:10]
            ]
            if not draft:
                hard.append("正文为空")
            hard += list(structure_gate(draft, template).issues)
            if policy.register_check:
                from .gate_classification import STYLE_CODES

                findings = check_register(draft, policy)
                hard += [
                    f.render()
                    for f in findings
                    if f.severity == "error" and f.code not in STYLE_CODES
                ]
                soft = [
                    f.render() for f in findings if f.severity == "warning" or f.code in STYLE_CODES
                ]
            else:
                soft = []
            audit = await reviewer.review(draft)
            if progress:
                await progress.save_context()
            hard.extend(audit["issues"])
            coverage = audit.get("requirements_review", {})
            hard.extend(coverage.get("issues", []))
            from .coverage_review import table_scope_issues

            hard.extend(table_scope_issues(draft))
            table_record = table_records.setdefault(draft, bb.scratch.get(TABLES_KEY) or {})
            hard.extend(
                await review_tables(
                    draft,
                    table_record,
                    [],
                    {},
                    ctx.llm_for("evidence_verifier"),
                    ctx.settings.llm_max_input_chars,
                    ledger=result.snapshot(),
                )
            )
            return Assessment(
                hard=hard,
                soft=soft,
                can_revise=audit["can_revise"] and coverage.get("can_revise", True),
            )

        def capture() -> dict[str, Any]:
            return {
                "body": current_body,
                "audit": reviewer.cached_review(current_body),
                "table_record": table_records.get(current_body, {}),
            }

        def restore(value: dict[str, Any]) -> bool:
            nonlocal current_body, seed_pending
            current_body = value["body"]
            seed_pending = False
            table_records[current_body] = value["table_record"]
            bb.scratch[TABLES_KEY] = table_records[current_body]
            return restore_prose(reviewer, current_body, value.get("audit"))

        if progress:
            progress.capture, progress.restore = capture, restore
        body = ""
        revision_log = None
        try:
            body, revision_log = await write_with_revisions(
                write, assess, max_revisions=policy.max_revisions, progress=progress
            )
        except (LeaseLostError, WritingProgressError):
            raise
        except Exception as exc:  # 写作失败不影响统计结果交付
            ctx.tracer.emit("SYNTHESIZER", "error", f"分析报告撰写失败，交付统计摘要：{exc}")
        unsupported = check_numbers(body, facts, input_description=question) if body else ["empty"]
        # 结构复核：报告必须按模板章节组织。缺章节多半意味着模型没按任务写
        # （例如泛泛而谈、或把别的任务的模板套了过来），此时统计摘要更可靠。
        if not unsupported and structure_gate(body, template).status != "pass":
            unsupported = ["missing_sections"]
        if unsupported:
            ctx.tracer.emit(
                "SYNTHESIZER",
                "info",
                "报告未通过数字或章节复核，已回退为统计摘要",
                data={
                    "report_validation": {
                        "issues": [
                            "missing_sections"
                            if unsupported == ["missing_sections"]
                            else "unsupported_number"
                        ],
                        "fallback": True,
                        "numbers": unsupported[:20],
                    }
                },
            )
            body = fallback_report(result)
        notice = "> 注意：未提供数据，本报告基于可复现的合成示例数据演示分析流程。\n\n"
        if result.synthetic and not body.startswith(notice):
            body = notice + body
        bb.report = Report(query=bb.query, markdown=body + "\n", citations=[])
        bb.scratch[TABLES_KEY] = table_records.get(body, {})
        bb.scratch[ANALYSIS_SCRATCH_KEY] = result.snapshot()
        # 图片不进 checkpoint：分析是确定性的（合成数据也用固定种子），交付层按
        # 同一份数据重算即可得到逐字节相同的图，checkpoint 不必背几百 KB 的 PNG。
        extras: dict[str, Any] = {"figures": len(result.figures)}
        extras[PROSE_REVIEW_KEY] = await reviewer.review(bb.report.markdown)
        extras[PROSE_REVIEW_KEY]["mechanically_finalized"] = True
        extras[PROSE_REVIEW_KEY]["body_replaced"] = bool(unsupported)
        # A recovered report replaces both copies of the previous review. The
        # report service reads the root record before the workbench extras.
        bb.scratch[PROSE_REVIEW_KEY] = extras[PROSE_REVIEW_KEY]
        bb.scratch["_report_validation"] = {
            "scope": "model_assessed_final_prose_support",
            "issues": list(extras[PROSE_REVIEW_KEY]["issues"]),
            "fallback": bool(unsupported),
            "semantic_verification": True,
            "support_status": extras[PROSE_REVIEW_KEY]["status"],
        }
        if revision_log is not None:
            extras["revision"] = revision_log.to_dict()
        bb.scratch[WORKBENCH_SCRATCH_KEY] = WriterState(
            template=template.key, extras=extras
        ).model_dump(mode="json")
        await finish(progress, bb, policy.max_revisions)
        return bb


__all__ = [
    "ANALYSIS_SCRATCH_KEY",
    "AnalysisResult",
    "DataAnalyst",
    "DatasetError",
    "allows_synthetic",
    "analyse",
    "check_numbers",
    "parse_dataset",
]
