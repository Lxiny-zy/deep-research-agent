"""Recorded diagnostics and multiple-comparison procedures for independent samples."""

from __future__ import annotations

import math
import warnings
from typing import Any


def _diagnostic(method: str, call: Any, **scope: Any) -> dict[str, Any]:
    from .analysis import _fmt, _fmt_p

    record = {**scope, "method": method, "alpha": 0.05}
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = call()
        statistic, p = float(result.statistic), float(result.pvalue)
        if not math.isfinite(statistic) or not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("统计量不可估计，不能据此确认前提成立")
        record.update(
            statistic=_fmt(statistic),
            p_value=_fmt_p(p),
            status="rejected" if p < 0.05 else "not_rejected",
            reason="拒绝该分布前提" if p < 0.05 else "未拒绝该分布前提，不等于证明前提成立",
            warnings=[str(item.message) for item in caught],
        )
    except (ValueError, FloatingPointError) as exc:
        record.update(statistic="NA", p_value="NA", status="unavailable", reason=str(exc))
    return record


def normality(values: Any, **scope: Any) -> dict[str, Any]:
    import numpy as np
    from scipy import stats

    method = "Shapiro-Wilk" if len(values) <= 5000 else "D'Agostino-Pearson"
    if len(values) < 3 or len(np.unique(values)) < 2:
        return {
            **scope,
            "kind": "normality",
            "method": method,
            "n": len(values),
            "alpha": 0.05,
            "status": "unavailable",
            "statistic": "NA",
            "p_value": "NA",
            "reason": "正态性检查至少需要 3 条有变异的有效观测，未认定前提成立",
        }
    return _diagnostic(
        method,
        lambda: stats.shapiro(values) if len(values) <= 5000 else stats.normaltest(values),
        kind="normality",
        n=len(values),
        **scope,
    )


def group_assumptions(
    grouped: list[tuple[str, Any]], variable: str, group: str
) -> list[dict[str, Any]]:
    from scipy import stats

    rows = [
        normality(values, variable=variable, group_column=group, group=label)
        for label, values in grouped
    ]
    rows.append(
        _diagnostic(
            "Levene (median)",
            lambda: stats.levene(*(values for _, values in grouped), center="median"),
            kind="variance_homogeneity",
            variable=variable,
            group_column=group,
            groups=[label for label, _ in grouped],
            n=sum(len(values) for _, values in grouped),
        )
    )
    rows.append(
        {
            "kind": "independence",
            "variable": variable,
            "group_column": group,
            "method": "design_requirement",
            "status": "not_assessed",
            "reason": "独立性取决于实验设计，不能由正态性或方差检验确认",
        }
    )
    return rows


def group_summaries(grouped: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    import numpy as np

    from .analysis import _fmt

    return [
        {
            "label": label,
            "n": len(values),
            "mean": _fmt(float(np.mean(values))),
            "std": _fmt(float(np.std(values, ddof=1))),
            "median": _fmt(float(np.median(values))),
        }
        for label, values in grouped
    ]


def welch_denominator_df(samples: list[Any]) -> float:
    import numpy as np

    n = np.asarray([len(sample) for sample in samples], dtype=float)
    variance = np.asarray([np.var(sample, ddof=1) for sample in samples], dtype=float)
    if np.any(variance <= 0) or not np.all(np.isfinite(variance)):
        return math.nan
    weights = n / variance
    term = np.sum((1 - weights / np.sum(weights)) ** 2 / (n - 1))
    return float((len(samples) ** 2 - 1) / (3 * term))


def posthoc_comparisons(grouped: list[tuple[str, Any]], *, equal_var: bool) -> list[dict[str, Any]]:
    from scipy import stats

    from .analysis import _fmt, _fmt_p

    result = stats.tukey_hsd(*(values for _, values in grouped), equal_var=equal_var)
    interval = result.confidence_interval(confidence_level=0.95)
    rows = []
    for i, (left, left_values) in enumerate(grouped):
        for j in range(i + 1, len(grouped)):
            right, right_values = grouped[j]
            p = float(result.pvalue[i, j])
            difference = -float(result.statistic[i, j])
            low, high = -float(interval.high[i, j]), -float(interval.low[i, j])
            available = (
                all(math.isfinite(value) for value in (p, difference, low, high)) and 0 <= p <= 1
            )
            rows.append(
                {
                    "left_group": left,
                    "right_group": right,
                    "method": "Tukey HSD" if equal_var else "Games-Howell",
                    "n_left": len(left_values),
                    "n_right": len(right_values),
                    "mean_difference": _fmt(difference),
                    "p_value": _fmt_p(p),
                    "significant": bool(p < 0.05) if available else None,
                    "ci_low": _fmt(low),
                    "ci_high": _fmt(high),
                    "confidence_level": 0.95,
                    "adjustment": "familywise",
                    "direction": "right-left",
                    "reason": (
                        "p 值低于数值分辨率，计算返回 0"
                        if available and p == 0
                        else ""
                        if available
                        else "样本不足或变异退化，事后比较不可估计"
                    ),
                }
            )
    return rows


def within_group_correlations(
    frame: Any, left: str, right: str, groups: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    from scipy import stats

    from .analysis import _fmt, _fmt_p

    rows = []
    issues = []
    for column in groups:
        for label, part in frame.groupby(column, dropna=True):
            pairs = part[[left, right]].dropna()
            row = {
                "a": left,
                "b": right,
                "group_column": column,
                "group": str(label),
                "n": len(pairs),
                "method": "Pearson",
                "adjustment": "none",
            }
            if len(pairs) < 3 or pairs[left].nunique() < 2 or pairs[right].nunique() < 2:
                issues.append(
                    f"{left} 与 {right} 在 {column}/{label} 的组内相关未执行："
                    f"有效观测 {len(pairs)} 对，至少需要 3 对且两变量均有变异。"
                )
                continue
            else:
                result = stats.pearsonr(pairs[left], pairs[right])
                row.update(
                    r=_fmt(float(result.statistic)), p_value=_fmt_p(float(result.pvalue)), reason=""
                )
            rows.append(row)
    return rows, issues


def method_notes(test: dict[str, Any]) -> list[str]:
    from .analysis import _p_text

    lines = []
    for item in test.get("assumptions", []):
        if item["kind"] == "independence":
            lines.append(f"  设计前提：{item['reason']}。")
            continue
        group = f"，{item['group']} 组" if "group" in item else ""
        sample = (
            f"，n={item['n']}" if item["kind"] == "normality" else f"，有效总样本量={item['n']}"
        )
        lines.append(
            f"  前提检查：{test['variable']} 按 {test['group']}{group}（{item['method']}）："
            f"统计量={item['statistic']}，{_p_text(item['p_value'])}{sample}；{item['reason']}。"
        )
    if test.get("assumptions"):
        if test["method"] == "Welch t 检验":
            lines.append("  Welch t 检验不要求两组方差相等；分布条件与独立性仍限制推断。")
        elif test["method"] == "Welch ANOVA":
            lines.append(
                "  方差齐性被拒绝或无法确认，采用 Welch ANOVA；分布条件与独立性仍限制推断。"
            )
    if test.get("posthoc"):
        lines.append(
            "  事后比较控制本变量、本分组列内全部两两比较的家族错误率；差值方向为右组减左组。"
        )
    for item in test.get("posthoc", []):
        lines.append(
            f"  {test['variable']} 按 {test['group']}，"
            f"{item['left_group']} 组与 {item['right_group']} 组"
            f"（{item['method']}）：平均差={item['mean_difference']}，{_p_text(item['p_value'])}；"
            f"95% 均值差置信区间=[{item['ci_low']}, {item['ci_high']}]；"
            + (
                "未达统计显著。"
                if item["significant"] is False
                else "差异达到统计显著。"
                if item["significant"] is True
                else f"{item['reason']}。"
            )
        )
    if test.get("posthoc_error"):
        lines.append(f"  {test['posthoc_error']}")
    return lines
