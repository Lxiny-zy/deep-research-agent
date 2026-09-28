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

import io
import math
import re
from dataclasses import dataclass, field
from typing import Any

from ..agents.base import Blackboard, RunContext
from ..models import Report
from ..registry import register
from .contract import contract_from_scratch
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

    def facts(self) -> str:
        """给写作段的数字台账（Markdown）。"""
        lines = [
            f"- 样本量：{self.rows} 行，{len(self.columns)} 列",
            f"- 数值变量：{', '.join(self.numeric) or '无'}",
            f"- 分类变量：{', '.join(self.categorical) or '无'}",
        ]
        if self.synthetic:
            lines.append("- 注意：用户未提供数据，以下为演示用合成数据")
        missing = {k: v for k, v in self.missing.items() if v}
        lines.append(f"- 缺失值：{missing or '无'}")
        lines.append("\n### 描述统计")
        for row in self.describe:
            lines.append(
                f"- {row['variable']}: n={row['n']}, 均值={row['mean']}, 标准差={row['std']}, "
                f"中位数={row['median']}, 最小={row['min']}, 最大={row['max']}"
            )
        if self.tests:
            lines.append("\n### 显著性检验")
            for test in self.tests:
                lines.append(
                    f"- {test['variable']} ~ {test['group']}（{test['method']}）："
                    f"统计量={test['statistic']}, p={test['p_value']}, "
                    f"{'显著' if test['significant'] else '不显著'}（α=0.05）"
                    + (
                        f"；稳健性复核 {test['robust_method']} p={test['robust_p']}"
                        if test.get("robust_method")
                        else ""
                    )
                )
        if self.correlations:
            lines.append("\n### 相关性（Pearson）")
            for item in self.correlations:
                lines.append(f"- {item['a']} 与 {item['b']}：r={item['r']}, p={item['p_value']}")
        if self.figures:
            lines.append("\n### 图表")
            lines += [f"- {fig.title}：{fig.caption}" for fig in self.figures]
        return "\n".join(lines)

    def summary_table(self) -> list[dict[str, Any]]:
        return self.describe


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


def analyse(csv_text: str, question: str = "") -> AnalysisResult:
    """对一张表做确定性分析。无数据时用可复现的合成示例演示并如实标注。"""
    import numpy as np
    import pandas as pd
    from scipy import stats

    synthetic = not csv_text.strip()
    frame = parse_dataset(_synthetic_csv() if synthetic else csv_text)
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

    tests: list[dict[str, Any]] = []
    for group in categorical:
        for variable in numeric:
            samples = [
                values.dropna().to_numpy()
                for _, values in frame.groupby(group, dropna=True)[variable]
            ]
            samples = [s for s in samples if len(s) >= 2]
            if len(samples) < 2:
                continue
            if len(samples) == 2:
                result = stats.ttest_ind(samples[0], samples[1], equal_var=False)
                method, robust = "Welch t 检验", stats.mannwhitneyu(samples[0], samples[1])
                robust_name = "Mann–Whitney U"
            else:
                result = stats.f_oneway(*samples)
                method, robust = "单因素方差分析", stats.kruskal(*samples)
                robust_name = "Kruskal–Wallis"
            p_value = float(result.pvalue)
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
                {"a": left, "b": right, "r": _fmt(float(r)), "p_value": _fmt(float(p))}
            )

    _chart_font()
    import matplotlib.pyplot as plt

    figures: list[Figure] = []
    for test in tests[:3]:
        fig, ax = plt.subplots(figsize=(6.4, 3.8))
        grouped = frame.groupby(test["group"])[test["variable"]]
        labels = [str(label) for label, _ in grouped]
        ax.boxplot([values.dropna().to_numpy() for _, values in grouped], tick_labels=labels)
        ax.set_title(f"{test['variable']} 按 {test['group']} 分组")
        ax.set_xlabel(test["group"])
        ax.set_ylabel(test["variable"])
        ax.grid(axis="y", alpha=0.3)
        figures.append(
            Figure(
                name=(
                    f"fig_{len(figures) + 1:02d}_box_"
                    f"{_safe(test['variable'])}_{_safe(test['group'])}.png"
                ),
                title=f"{test['variable']} 按 {test['group']} 分组箱线图",
                caption=f"统计口径：每组有效样本；{test['method']} p={test['p_value']}",
                png=_png(fig),
            )
        )
    if not tests and numeric:
        column = numeric[0]
        fig, ax = plt.subplots(figsize=(6.4, 3.8))
        ax.hist(frame[column].dropna().to_numpy(), bins=min(20, max(5, len(frame) // 5)))
        ax.set_title(f"{column} 分布")
        ax.set_xlabel(column)
        ax.set_ylabel("频数")
        figures.append(
            Figure(
                name=f"fig_01_hist_{_safe(column)}.png",
                title=f"{column} 分布直方图",
                caption=f"统计口径：{column} 的全部非缺失值（n={int(frame[column].count())}）",
                png=_png(fig),
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
        figures.append(
            Figure(
                name=f"fig_{len(figures) + 1:02d}_correlation.png",
                title="数值变量相关矩阵",
                caption="统计口径：两两成对删除缺失值后的 Pearson r",
                png=_png(fig),
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
    )


def _looks_like_identifier(series: Any) -> bool:
    """编号列（1..k 的连续整数、每个值重复出现）不是测量值：对它求均值或做检验没有意义。"""
    values = series.dropna()
    if values.empty or not (values == values.round()).all():
        return False
    unique = sorted(set(int(v) for v in values))
    contiguous = unique == list(range(unique[0], unique[0] + len(unique)))
    return contiguous and len(unique) < len(values) and len(unique) <= 50


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:24] or "var"


def _numbers(text: str) -> set[str]:
    return {m.rstrip(".") for m in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?:e[-+]?\d+)?", text)}


def check_numbers(body: str, facts: str) -> list[str]:
    """正文里出现、却不在统计台账里的数字（排除章节序号与 α=0.05 这类常量）。"""
    allowed = _numbers(facts) | {"0.05", "0", "1", "2", "3", "4", "5", "10", "95", "100"}
    stripped = re.sub(r"(?m)^\s*#+.*$|^\s*\d+[.)、]\s", "", body)
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
        methods.append(
            "按分类变量分组的显著性检验（两组 Welch t / 多组单因素方差分析，附非参数稳健性复核）"
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
        verdict = "存在显著差异" if test["significant"] else "未发现显著差异"
        conclusions.append(
            f"- {test['variable']} 在不同 {test['group']} 之间{verdict}"
            f"（{test['method']}，p={test['p_value']}；"
            f"{test['robust_method']} p={test['robust_p']}）。"
        )
    for item in result.correlations:
        conclusions.append(
            f"- {item['a']} 与 {item['b']} 的 Pearson r={item['r']}（p={item['p_value']}）。"
        )
    if not conclusions:
        conclusions.append("- 数据中没有可检验的分组或相关关系，仅给出描述统计。")
    conclusions.append("- 以上结论均由统计代码直接得出；显著性阈值 α=0.05，未做多重比较校正。")
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
        ctx.tracer.emit("RESEARCHER", "start", "解析数据并执行统计分析…")
        try:
            result = await run_blocking(analyse, csv_text, question)
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
        )
        user = f"分析问题：{question or '对数据做探索性分析'}\n\n## 统计台账\n{facts}\n"
        from .gates import structure_gate
        from .quality import coerce_policy
        from .revision import Assessment, write_with_revisions
        from .scholarly import check_register

        policy = coerce_policy(contract.quality if contract is not None else ctx.settings.quality)

        async def write(revision: str | None) -> str:
            chunks: list[str] = []
            async for delta in ctx.llm_for(self.name).stream(
                system, user + (revision or ""), temperature=0.2
            ):
                ctx.tracer.emit("SYNTHESIZER", "token", data={"delta": delta})
                chunks.append(delta)
            return "".join(chunks).strip()

        def assess(draft: str) -> Assessment:
            # 数字与章节是硬性要求；文体问题同样要求修订。三者都是确定性检查。
            hard = [f"数字「{n}」不在统计台账中" for n in check_numbers(draft, facts)[:10]]
            if not draft:
                hard.append("正文为空")
            hard += list(structure_gate(draft, template).issues)
            if policy.register_check:
                findings = check_register(draft, policy)
                hard += [f.render() for f in findings if f.severity == "error"]
                soft = [f.render() for f in findings if f.severity == "warning"]
            else:
                soft = []
            return Assessment(hard=hard, soft=soft)

        body = ""
        revision_log = None
        try:
            body, revision_log = await write_with_revisions(
                write, assess, max_revisions=policy.max_revisions
            )
        except Exception as exc:  # 写作失败不影响统计结果交付
            ctx.tracer.emit("SYNTHESIZER", "error", f"分析报告撰写失败，交付统计摘要：{exc}")
        unsupported = check_numbers(body, facts) if body else ["empty"]
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
        if result.synthetic:
            body = "> 注意：未提供数据，本报告基于可复现的合成示例数据演示分析流程。\n\n" + body
        bb.report = Report(query=bb.query, markdown=body + "\n", citations=[])
        bb.scratch[ANALYSIS_SCRATCH_KEY] = {
            "rows": result.rows,
            "columns": result.columns,
            "describe": result.describe,
            "tests": result.tests,
            "correlations": result.correlations,
            "synthetic": result.synthetic,
            "figures": [
                {"name": f.name, "title": f.title, "caption": f.caption} for f in result.figures
            ],
        }
        # 图片不进 checkpoint：分析是确定性的（合成数据也用固定种子），交付层按
        # 同一份数据重算即可得到逐字节相同的图，checkpoint 不必背几百 KB 的 PNG。
        extras: dict[str, Any] = {"figures": len(result.figures)}
        if revision_log is not None:
            extras["revision"] = revision_log.to_dict()
        bb.scratch[WORKBENCH_SCRATCH_KEY] = WriterState(
            template=template.key, extras=extras
        ).model_dump(mode="json")
        return bb


__all__ = [
    "ANALYSIS_SCRATCH_KEY",
    "AnalysisResult",
    "DataAnalyst",
    "DatasetError",
    "analyse",
    "check_numbers",
    "parse_dataset",
]
