"""结构化报告文档、内联 SVG 图表与 Markdown 投影。

测试重点不在"能不能画出图",而在几条会决定报告可信度的性质:

* **图表必有源表**——``ChartBlock`` 不含数据,引用不存在的源表/列必须失败,
  而不是画出一张空图;
* **缺失如实呈现**——未报告的单元格三种格式都写"未报告",不补零、不留空;
* **柱状图零基线**——柱长即数值,截断 Y 轴是撒谎;要看小差异就换 ``dot``;
* **单系列不按数值上色**——方法名是无序名义类别,深浅上色会把柱长重复编码成色相;
* **tooltip-only 信息被提升为正式内容**——验证理由、置信度、完整哈希在纯文本
  投影里必须出现,因为 tooltip 打印不输出、触屏不可达;
* **SVG 输出确定性**——同一输入逐字节一致,否则无法做版本回归。
"""

from __future__ import annotations

import re

import pytest

from deep_research.report import (
    ChartBlock,
    ChartDataError,
    EvidenceRecord,
    Overview,
    ProseBlock,
    ReportDocument,
    TableBlock,
    TableCell,
    TableColumn,
    TableRow,
    render_chart,
    render_markdown,
)

# 一张真实形状的 CASSI 重建 benchmark 表:方法名长、指标多、必然有列缺失
# (早期方法不报 FLOPs),而且 PSNR 是"35 dB 基座上比 1 dB 差异"的典型。
BENCH = TableBlock(
    id="recon_benchmark",
    title="重建算法在 KAIST 仿真集上的表现",
    columns=[
        TableColumn(key="psnr", label="PSNR", unit="dB", align="right", numeric=True),
        TableColumn(key="psnr_real", label="真实数据 PSNR", unit="dB", align="right", numeric=True),
        TableColumn(key="ssim", label="SSIM", align="right", numeric=True),
        TableColumn(key="params", label="参数量", unit="M", align="right", numeric=True),
        TableColumn(key="kind", label="类别"),
    ],
    rows=[
        TableRow(
            label="TSA-Net",
            citation=1,
            cells={
                "psnr": TableCell(value="31.46", numeric=31.46, citations=[1], note_ref=1),
                "psnr_real": TableCell(value="27.81", numeric=27.81, citations=[1]),
                "ssim": TableCell(value="0.894", numeric=0.894, citations=[1]),
                "params": TableCell(value="44.25", numeric=44.25, citations=[1]),
                "kind": TableCell(value="端到端深度"),
            },
        ),
        TableRow(
            label="MST-L",
            citation=2,
            cells={
                "psnr": TableCell(value="35.18", numeric=35.18, citations=[2], note_ref=1),
                "psnr_real": TableCell(value="29.04", numeric=29.04, citations=[2]),
                "ssim": TableCell(value="0.948", numeric=0.948, citations=[2]),
                "params": TableCell(value="2.03", numeric=2.03, citations=[2]),
                "kind": TableCell(value="端到端深度"),
            },
        ),
        TableRow(
            label="DAUHST-9stg",
            citation=3,
            cells={
                "psnr": TableCell(value="38.36", numeric=38.36, citations=[3], note_ref=1),
                "psnr_real": TableCell(value="30.12", numeric=30.12, citations=[3]),
                "ssim": TableCell(value="0.967", numeric=0.967, citations=[3]),
                # 该论文未报参数量:必须如实留空,不能补零
                "params": TableCell(),
                "kind": TableCell(value="深度展开"),
            },
        ),
    ],
    notes=["全部为 28 波段、256×256 仿真口径；不同 mask 与训练集下的数值不可直接比较。"],
)


def _doc(*blocks) -> ReportDocument:
    return ReportDocument(query="CASSI 重建方法对比", blocks=list(blocks))


# ── 不变量:图必有源表 ───────────────────────────────────────────────────────


def test_chart_carries_no_data_of_its_own() -> None:
    """图表凭空造数在类型层面就不可表达——ChartBlock 上没有任何数据字段。"""
    chart = ChartBlock(id="c1", source_table="recon_benchmark", value_columns=["psnr"])

    assert not hasattr(chart, "data")
    assert not hasattr(chart, "values")
    assert chart.source_table == "recon_benchmark"


def test_chart_rejects_a_mismatched_source_table() -> None:
    chart = ChartBlock(id="c1", source_table="other_table", value_columns=["psnr"])

    with pytest.raises(ChartDataError, match="源表"):
        render_chart(chart, BENCH)


def test_chart_rejects_a_column_that_does_not_exist() -> None:
    """列名写错必须失败,而不是静默画出一张全是缺失的空图。"""
    chart = ChartBlock(id="c1", source_table=BENCH.id, value_columns=["psnr", "flops"])

    with pytest.raises(ChartDataError, match="flops"):
        render_chart(chart, BENCH)


def test_chart_rejects_more_series_than_the_validated_palette_supports() -> None:
    with pytest.raises(ValueError):
        ChartBlock(
            id="c1",
            source_table=BENCH.id,
            value_columns=["psnr", "ssim", "params", "kind"],
        )


def test_markdown_rejects_a_chart_whose_source_table_is_absent() -> None:
    chart = ChartBlock(id="c1", source_table="missing", value_columns=["psnr"])

    with pytest.raises(ChartDataError, match="missing"):
        render_markdown(_doc(chart))


# ── 零基线与形式选择 ─────────────────────────────────────────────────────────


def test_bar_chart_starts_at_zero() -> None:
    """柱长即数值,基线必须为零——否则 31 dB 与 38 dB 会看起来差三倍。"""
    chart = ChartBlock(id="c1", source_table=BENCH.id, form="bar", value_columns=["psnr"])

    svg = render_chart(chart, BENCH)

    ticks = [float(t) for t in re.findall(r'class="dr-chart-tick"[^>]*>([\d.]+)</text>', svg)]
    assert 0.0 in ticks, f"柱状图刻度必须含 0，实际 {ticks}"


def test_dot_chart_may_use_a_non_zero_baseline() -> None:
    """点用位置编码,没有"从零开始"的语义,所以放大差异是诚实的。

    这是 35 dB 基座上看 1 dB 差异的正解——而不是去截断柱状图的 Y 轴。
    """
    chart = ChartBlock(id="c1", source_table=BENCH.id, form="dot", value_columns=["psnr"])

    svg = render_chart(chart, BENCH)

    ticks = [float(t) for t in re.findall(r'class="dr-chart-tick"[^>]*>([\d.]+)</text>', svg)]
    assert ticks and min(ticks) > 0.0, f"点图不应被迫从 0 起,实际 {ticks}"


def test_single_series_uses_one_colour_for_every_mark() -> None:
    """方法名是无序名义类别:按数值深浅上色会把柱长重复编码成色相。"""
    chart = ChartBlock(id="c1", source_table=BENCH.id, form="bar", value_columns=["psnr"])

    svg = render_chart(chart, BENCH)

    fills = set(re.findall(r'class="dr-chart-mark" fill="([^"]+)"', svg))
    assert fills == {"var(--dr-s1)"}


def test_emphasis_greys_out_everything_except_the_highlighted_row() -> None:
    """ "其中某一个是重点"用强调形式表达,不靠分类色。"""
    chart = ChartBlock(
        id="c1", source_table=BENCH.id, form="bar", value_columns=["psnr"], emphasis="MST-L"
    )

    svg = render_chart(chart, BENCH)

    fills = re.findall(r'class="dr-chart-mark" fill="([^"]+)"', svg)
    assert fills.count("var(--dr-s1)") == 1
    assert fills.count("var(--dr-muted-mark)") == 2


def test_single_series_has_no_legend_but_multi_series_does() -> None:
    """一种颜色时标题已说明画的是什么,图例只会重复标题并占地方。"""
    single = render_chart(ChartBlock(id="c1", source_table=BENCH.id, value_columns=["psnr"]), BENCH)
    multi = render_chart(
        ChartBlock(
            id="c2",
            source_table=BENCH.id,
            form="grouped_bar",
            value_columns=["psnr", "psnr_real"],
        ),
        BENCH,
    )

    assert "dr-chart-legend" not in single
    assert "dr-chart-legend" in multi
    assert "PSNR（dB）" in multi and "真实数据 PSNR（dB）" in multi


def test_multi_series_with_mixed_units_is_refused() -> None:
    """value_columns 共用一根数值轴,量纲不同就是双 Y 轴撒谎。

    PSNR(31–38 dB) 与 SSIM(0.89–0.97) 并排放到 0–40 的轴上,SSIM 会渲染成约 8px
    的残根——那个系列的信息被完全抹掉,而图看上去仍然人模人样。
    """
    chart = ChartBlock(
        id="c1", source_table=BENCH.id, form="grouped_bar", value_columns=["psnr", "ssim"]
    )

    with pytest.raises(ChartDataError, match="量纲不一致"):
        render_chart(chart, BENCH)


def test_scatter_may_pair_two_different_measures_on_its_two_axes() -> None:
    """散点图的两根轴本来就是两个度量,这不是双 Y 轴——量纲检查不该误伤它。"""
    chart = ChartBlock(
        id="c1", source_table=BENCH.id, form="scatter", x_column="params", value_columns=["psnr"]
    )

    svg = render_chart(chart, BENCH)

    assert "dr-chart-dot" in svg


def test_long_chinese_row_labels_are_clipped_by_display_width() -> None:
    """ "裁到 N 个字符"在中文下是错的口径:24 个汉字约 288px,标签区只有 140px。"""
    table = TableBlock(
        id="t",
        columns=[TableColumn(key="v", label="值", unit="dB", numeric=True)],
        rows=[
            TableRow(
                label="基于衍射光学元件的快照式高光谱成像系统方案",
                cells={"v": TableCell(value="30", numeric=30.0)},
            )
        ],
    )

    svg = render_chart(ChartBlock(id="c", source_table="t", value_columns=["v"]), table)

    label = re.search(r'class="dr-chart-cat"[^>]*>([^<]+)</text>', svg)
    assert label is not None
    text = label.group(1)
    assert text.endswith("…")
    # 全角计 2、半角计 1，总显示宽度不得超出标签区
    assert sum(2 if ord(ch) > 0x2E7F else 1 for ch in text) <= 21


# ── 缺失如实呈现 ─────────────────────────────────────────────────────────────


def test_chart_marks_an_unreported_cell_instead_of_plotting_zero() -> None:
    """DAUHST 未报参数量。画成 0 会让它看起来是"零参数",这是最糟的补全。"""
    chart = ChartBlock(id="c1", source_table=BENCH.id, form="bar", value_columns=["params"])

    svg = render_chart(chart, BENCH)

    assert "未报告" in svg
    # 三行里只有两行有柱
    assert len(re.findall(r'class="dr-chart-mark"', svg)) == 2


def test_markdown_table_writes_unreported_rather_than_leaving_a_blank() -> None:
    """空白会被读成"零"或"排版问题",两种误读都比显式承认缺失更糟。"""
    md = render_markdown(_doc(BENCH))

    dauhst = next(line for line in md.splitlines() if line.startswith("| DAUHST-9stg"))
    assert "未报告" in dauhst


# ── Markdown 投影 ────────────────────────────────────────────────────────────


def test_markdown_table_keeps_per_cell_citations_and_protocol_notes() -> None:
    """逐格引用与口径脚注是表格可信的全部依据,不能在投影时丢掉。"""
    md = render_markdown(_doc(BENCH))

    assert "35.18 [2]（注 1）" in md
    assert "**口径脚注**" in md
    assert "28 波段、256×256 仿真口径" in md
    # 数值列右对齐用 GFM 的 ---: ,这是支持面最广的对齐语法
    assert "| ---:" in md


def test_markdown_degrades_a_chart_into_its_source_table_losslessly() -> None:
    """MD 渲染不了矢量图,但源表结构上必然存在,所以一个数字都不会少。"""
    chart = ChartBlock(
        id="c1", source_table=BENCH.id, form="bar", value_columns=["psnr"], title="PSNR 对比"
    )

    md = render_markdown(_doc(chart, BENCH))

    assert "## PSNR 对比" in md
    assert "见表《重建算法在 KAIST 仿真集上的表现》" in md
    for value in ("31.46", "35.18", "38.36"):
        assert value in md, f"降级后仍必须包含 {value}"


def test_markdown_does_not_print_the_same_table_twice() -> None:
    """图只引用源表——同一份数字出现两遍会被读成两组数据。"""
    chart = ChartBlock(
        id="c1", source_table=BENCH.id, form="bar", value_columns=["psnr"], title="PSNR 对比"
    )

    md = render_markdown(_doc(chart, BENCH))

    assert md.count("| DAUHST-9stg") == 1


def test_markdown_states_the_disclaimer_exactly_once() -> None:
    md = render_markdown(
        ReportDocument(
            query="q",
            blocks=[ProseBlock(markdown="正文")],
            evidence=[EvidenceRecord(citation=1, statement="s")],
        )
    )

    assert md.count("不保证论断在开放世界为真") == 1


def test_markdown_uses_only_the_portable_subset() -> None:
    """不用脚注语法/<details>/锚点跳转——各家 Markdown 支持面不一致。"""
    md = render_markdown(
        ReportDocument(
            query="q",
            blocks=[ProseBlock(markdown="正文 [1]"), BENCH],
            evidence=[EvidenceRecord(citation=1, statement="s", quote="q", content_hash="ab" * 32)],
        )
    )

    assert "[^" not in md
    assert "<details>" not in md
    assert "](#" not in md


def test_markdown_escapes_pipes_that_would_break_a_table_row() -> None:
    table = TableBlock(
        id="t",
        columns=[TableColumn(key="a", label="A")],
        rows=[TableRow(label="含 | 管道", cells={"a": TableCell(value="x | y")})],
    )

    md = render_markdown(_doc(table))

    row = next(line for line in md.splitlines() if "管道" in line)
    # 只数未被转义的管道符：结构上应只有首、中、尾三个
    assert len(re.findall(r"(?<!\\)\|", row)) == 3, f"转义后该行结构管道符应为 3：{row}"


# ── 证据附录:tooltip-only 信息必须落地 ──────────────────────────────────────


def test_evidence_appendix_promotes_tooltip_only_fields_to_real_content() -> None:
    """验证理由、语义置信度、印证说明原先只活在 HTML title= 里。

    tooltip 打印不输出、触屏不可达,靠它承载信息在 HTML 移动端就已经在丢。
    """
    record = EvidenceRecord(
        citation=1,
        claim_id="C-7",
        statement="DAUHST 在 KAIST 上达到 38.36 dB",
        quote="38.36 dB",
        context="Our method achieves 38.36 dB on the KAIST simulation benchmark.",
        reference="Cai 等. DAUHST. NeurIPS, 2022. https://arxiv.org/abs/2205.10102",
        content_hash="cd" * 32,
        verbatim_verified=True,
        verification_reason="quote_found_in_source",
        semantic_status="supported",
        semantic_confidence=0.92,
        semantic_reason="原文数值与论断一致",
        consistency_status="clear",
        corroboration_status="corroborated",
        independent_source_count=2,
        corroboration_reason="两个独立发布方报告同一数值",
    )

    md = render_markdown(ReportDocument(query="q", evidence=[record]))

    assert "## 证据附录" in md
    assert "**验证说明**：quote_found_in_source" in md
    assert "置信度 92%" in md
    assert "**印证说明**：两个独立发布方报告同一数值" in md
    assert "已交叉印证 · 2 个独立来源" in md
    # 完整哈希,不截断:截断版本无法用来核对快照
    assert "cd" * 32 in md
    assert "`C-7`" in md


def test_evidence_appendix_emphasises_the_verbatim_quote_inside_its_context() -> None:
    """HTML 用 <mark> 高亮,纯文本用加粗——读者要能看出哪一段是逐字证据。"""
    record = EvidenceRecord(
        citation=1,
        statement="s",
        quote="38.36 dB",
        context="Our method achieves 38.36 dB on KAIST.",
    )

    md = render_markdown(ReportDocument(evidence=[record]))

    assert "Our method achieves **38.36 dB** on KAIST." in md


def test_conflicted_evidence_reports_the_reason_and_the_other_claims() -> None:
    """冲突不能被静默丢弃——它必须出现在读者看得到的地方。"""
    record = EvidenceRecord(
        citation=1,
        statement="该方法达到 38.36 dB",
        consistency_status="conflicted",
        contradicts_claim_ids=["C-9", "C-11"],
        contradiction_reason="另一来源报告同一配置下为 37.21 dB",
    )

    md = render_markdown(ReportDocument(evidence=[record]))

    assert "存在冲突" in md
    assert "37.21 dB" in md
    assert "C-9, C-11" in md


def test_overview_states_when_the_blocked_count_is_unavailable() -> None:
    """拿不到审计事件时说"不可用",不写 0——0 是个具体断言,而我们并不知道。"""
    md = render_markdown(
        ReportDocument(overview=Overview(records=4, verbatim_matched=4, blocked_sources=None))
    )

    assert "不可用" in md
    assert "| 来源被拦截 | 0 |" not in md


def test_disclaimer_appears_and_scopes_the_claim() -> None:
    md = render_markdown(_doc(ProseBlock(markdown="正文")))

    assert "不保证论断在开放世界为真" in md


# ── SVG 输出性质 ─────────────────────────────────────────────────────────────


def test_svg_is_deterministic_for_the_same_input() -> None:
    """版本回归依赖逐字节可比;浮点格式化必须收敛。"""
    chart = ChartBlock(id="c1", source_table=BENCH.id, value_columns=["psnr"])

    assert render_chart(chart, BENCH) == render_chart(chart, BENCH)


def test_svg_scales_and_reserves_room_for_the_axis_band() -> None:
    """viewBox 高度必须包含轴标签带,否则容器会把刻度裁掉。"""
    chart = ChartBlock(id="c1", source_table=BENCH.id, value_columns=["psnr"])

    svg = render_chart(chart, BENCH)

    match = re.search(r'viewBox="0 0 720 ([\d.]+)"', svg)
    assert match is not None
    # 3 行 × 30 + 上边距 10 + 轴带 34
    assert float(match.group(1)) == pytest.approx(134.0)


def test_every_mark_carries_a_native_tooltip_without_javascript() -> None:
    """<title> 是零 JS 的交互层,自包含导出与打印预览都保留它。"""
    chart = ChartBlock(id="c1", source_table=BENCH.id, value_columns=["psnr"])

    svg = render_chart(chart, BENCH)

    assert "<title>MST-L · PSNR: 35.18 dB  [2]</title>" in svg


def test_chart_caption_points_back_at_the_source_table() -> None:
    """读者要能去核每一个点,而不是只能相信这张图。"""
    chart = ChartBlock(id="c1", source_table=BENCH.id, value_columns=["psnr"])

    svg = render_chart(chart, BENCH)

    assert "数据取自：重建算法在 KAIST 仿真集上的表现" in svg


def test_chart_is_labelled_for_assistive_technology() -> None:
    chart = ChartBlock(id="c1", source_table=BENCH.id, value_columns=["psnr"], title="PSNR 对比")

    svg = render_chart(chart, BENCH)

    assert 'role="img"' in svg
    assert 'aria-label="PSNR 对比"' in svg
    assert "<desc>" in svg and "完整数值见源表" in svg


def test_bars_are_capped_in_thickness_and_rounded_only_at_the_data_end() -> None:
    """数据端圆角标记"值到这里";基线端方角,圆掉会让人以为那侧也是数据。"""
    chart = ChartBlock(id="c1", source_table=BENCH.id, form="bar", value_columns=["psnr"])

    svg = render_chart(chart, BENCH)

    path = re.search(r'class="dr-chart-mark" fill="[^"]+" d="([^"]+)"', svg)
    assert path is not None
    d = path.group(1)
    # 两个二次贝塞尔角 = 只有一端圆角
    assert d.count("Q") == 2


def test_scatter_skips_missing_points_without_interpolating() -> None:
    """缺值断开,不插值——插出来的点就是编出来的数据。"""
    chart = ChartBlock(
        id="c1", source_table=BENCH.id, form="scatter", x_column="params", value_columns=["psnr"]
    )

    svg = render_chart(chart, BENCH)

    # DAUHST 缺 params,只剩两个点
    assert len(re.findall(r'class="dr-chart-dot"', svg)) == 2


def test_line_chart_breaks_at_a_missing_value() -> None:
    chart = ChartBlock(
        id="c1", source_table=BENCH.id, form="line", x_column="params", value_columns=["psnr"]
    )

    svg = render_chart(chart, BENCH)

    path = re.search(r'class="dr-chart-line" stroke="[^"]+" d="([^"]+)"', svg)
    assert path is not None
    assert path.group(1).count("L") == 1  # 两个点一段线


def test_chart_text_never_wears_the_series_colour() -> None:
    """浅色系列色作为文字在浅背景上不可读;身份由文字旁边的色块承载。"""
    chart = ChartBlock(
        id="c1", source_table=BENCH.id, form="grouped_bar", value_columns=["psnr", "psnr_real"]
    )

    svg = render_chart(chart, BENCH)

    for text_tag in re.findall(r"<text[^>]*>", svg):
        assert "var(--dr-s" not in text_tag, f"文字带了系列色：{text_tag}"
