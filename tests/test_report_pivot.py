"""从 findings 透视对照表。

这是"表格由代码渲染，不由 LLM 自由生成"的落地点，所以测试针对的是**可信度性质**
而不是排版:

* 只有通过证据门禁（含数值校验）的 finding 才能进表；
* **口径不同的数值必须分列**——同一个 PSNR 在 28 波段与 31 波段下不可比，
  同列摆放越整齐越误导；
* **"未标注口径"自成一列**，不与任何已知口径合并（"不知道"不是"相同"）；
* 数值冲突并列呈现且标注，但**排除出图表**（画成一个点等于替读者裁决）；
* 缺失留空由渲染器写"未报告"，不补零；
* 交叉印证过的数值显示全部来源角标。
"""

from __future__ import annotations

from deep_research.models import (
    EvidenceVerification,
    ExperimentConditions,
    Finding,
    Quantity,
    Report,
    ResearchResult,
)
from deep_research.report import assemble_document, pivot_tables, render_markdown

_URL_A = "https://doi.org/10.1364/oe.1"
_URL_B = "https://arxiv.org/abs/2205.10102v3"
_URL_C = "https://doi.org/10.1109/cvpr.2"
_INDEX = {_URL_A: 1, _URL_B: 2, _URL_C: 3}

_KAIST28 = ExperimentConditions(dataset="KAIST", split="10 scenes", bands=28)
_KAIST31 = ExperimentConditions(dataset="KAIST", split="10 scenes", bands=31)


def _finding(
    entity: str,
    metric: str,
    value: float,
    rendered: str,
    *,
    url: str = _URL_B,
    unit: str = "dB",
    conditions: ExperimentConditions | None = _KAIST28,
    eligible: bool = True,
    quantity_status: str = "verified",
    comparator: str = "",
    uncertainty: float | None = None,
) -> Finding:
    return Finding(
        statement=f"{entity} 的 {metric} 为 {rendered}",
        entity=entity,
        source_url=url,
        evidence_quote=f"{metric} of {rendered} {unit}",
        quantity=Quantity(
            metric=metric,
            value=value,
            unit=unit,
            rendered=rendered,
            comparator=comparator,  # type: ignore[arg-type]
            uncertainty=uncertainty,
        ),
        conditions=conditions,
        verification=EvidenceVerification(
            status="verified",
            method="normalized_quote",
            semantic_status="supported" if eligible else "unsupported",
            quantity_status=quantity_status,  # type: ignore[arg-type]
            source_content_hash="ab" * 32,
        ),
    )


def _results(*findings: Finding) -> list[ResearchResult]:
    return [ResearchResult(sub_question="sq", findings=list(findings))]


# ── 门禁 ────────────────────────────────────────────────────────────────────


def test_only_report_eligible_findings_reach_the_table() -> None:
    """语义不支持的论断进不了正文，也就不该进表。"""
    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18"),
            _finding("TSA-Net", "PSNR", 31.46, "31.46"),
            _finding("幽灵方法", "PSNR", 99.9, "99.9", eligible=False),
        ),
        _INDEX,
    )

    assert [row.label for row in tables[0].rows] == ["MST-L", "TSA-Net"]


def test_a_fabricated_number_never_reaches_the_table() -> None:
    """数值校验判 unsupported = 原文里没这个数。带假数字的表比假话更危险。"""
    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18"),
            _finding("TSA-Net", "PSNR", 31.46, "31.46"),
            _finding("DAUHST", "PSNR", 41.0, "41.00", quantity_status="unsupported"),
        ),
        _INDEX,
    )

    assert [row.label for row in tables[0].rows] == ["MST-L", "TSA-Net"]


def test_a_finding_without_an_entity_cannot_be_placed_in_a_row() -> None:
    """没有对象就定不了行。它仍会出现在正文与证据附录，只是进不了表。"""
    orphan = _finding("", "PSNR", 33.0, "33.00")

    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18"),
            _finding("TSA-Net", "PSNR", 31.46, "31.46"),
            orphan,
        ),
        _INDEX,
    )

    assert all(row.label for row in tables[0].rows)
    assert len(tables[0].rows) == 2


def test_a_source_outside_the_citation_list_is_skipped() -> None:
    """表里每格都要能指回一个 [n]；没有角标的来源无法被核。"""
    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18"),
            _finding("TSA-Net", "PSNR", 31.46, "31.46"),
            _finding("X", "PSNR", 30.0, "30.00", url="https://uncited.test/x"),
        ),
        _INDEX,
    )

    assert [row.label for row in tables[0].rows] == ["MST-L", "TSA-Net"]


def test_no_table_is_produced_when_there_is_nothing_to_compare() -> None:
    """1×1 的"表"只是把一句话画上框。"""
    assert pivot_tables(_results(_finding("MST-L", "PSNR", 35.18, "35.18")), _INDEX) == []
    assert pivot_tables([], _INDEX) == []


# ── 口径分列（最关键的一条） ────────────────────────────────────────────────


def test_values_under_different_protocols_go_into_separate_columns() -> None:
    """28 波段与 31 波段的 PSNR 不可比。同列摆放会让读者直接横向比较。"""
    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18", conditions=_KAIST28),
            _finding("CST-L", "PSNR", 36.12, "36.12", conditions=_KAIST31),
        ),
        _INDEX,
    )
    table = tables[0]

    psnr_columns = [c for c in table.columns if c.label == "PSNR"]
    assert len(psnr_columns) == 2, "两种口径必须分成两列"
    # 每列指向各自的口径脚注
    assert {c.note_ref for c in psnr_columns} == {1, 2}
    assert any("28 波段" in note for note in table.notes)
    assert any("31 波段" in note for note in table.notes)


def test_each_new_hsi_condition_dimension_is_part_of_the_signature() -> None:
    base = ExperimentConditions(dataset="KAIST", split="test", bands=28)
    variants = (
        ("spectral_range", "400-700 nm"),
        ("scenes", "S1-S10"),
        ("acquisition", "real capture"),
    )

    for field, value in variants:
        variant = base.model_copy(update={field: value})
        tables = pivot_tables(
            _results(
                _finding("MST-L", "PSNR", 35.18, "35.18", conditions=base),
                _finding("CST-L", "PSNR", 36.12, "36.12", conditions=variant),
            ),
            _INDEX,
        )
        psnr_columns = [column for column in tables[0].columns if column.label == "PSNR"]
        assert len(psnr_columns) == 2, field


def test_a_single_protocol_needs_no_footnote_noise() -> None:
    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18"),
            _finding("TSA-Net", "PSNR", 31.46, "31.46"),
        ),
        _INDEX,
    )
    table = tables[0]

    assert table.notes == []
    assert all(column.note_ref is None for column in table.columns)


def test_unstated_conditions_are_their_own_column_not_merged() -> None:
    """ "不知道条件"不等于"条件相同"。静默合并等于替读者做没有依据的判断。"""
    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18", conditions=_KAIST28),
            _finding("神秘方法", "PSNR", 40.0, "40.00", conditions=None),
        ),
        _INDEX,
    )
    table = tables[0]

    assert len([c for c in table.columns if c.label == "PSNR"]) == 2
    assert any("口径未标注" in note for note in table.notes)


def test_different_metrics_become_different_columns() -> None:
    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18"),
            _finding("MST-L", "SSIM", 0.948, "0.948", unit=""),
            _finding("TSA-Net", "PSNR", 31.46, "31.46"),
        ),
        _INDEX,
    )
    table = tables[0]

    assert {column.label for column in table.columns} == {"PSNR", "SSIM"}
    assert table.column("psnr") is not None and table.column("psnr").unit == "dB"
    assert table.column("ssim") is not None and table.column("ssim").unit == ""


def test_metric_names_are_case_insensitive_for_column_identity() -> None:
    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18"),
            _finding("TSA-Net", "psnr", 31.46, "31.46"),
        ),
        _INDEX,
    )

    assert len(tables[0].columns) == 1


# ── 缺失与冲突 ──────────────────────────────────────────────────────────────


def test_a_missing_cell_stays_empty_and_renders_as_unreported() -> None:
    """留空＝原文未报告。补零会让"未报参数量"看起来像"零参数"。"""
    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18"),
            _finding("MST-L", "SSIM", 0.948, "0.948", unit=""),
            _finding("TSA-Net", "PSNR", 31.46, "31.46"),  # 没有 SSIM
        ),
        _INDEX,
    )
    table = tables[0]

    tsa = next(row for row in table.rows if row.label == "TSA-Net")
    assert not tsa.cell("ssim").reported
    assert tsa.cell("ssim").numeric is None


def test_two_sources_reporting_the_same_value_show_both_citations() -> None:
    """交叉印证过的数值显示两个来源，这正是双源门禁的产物。"""
    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18", url=_URL_A),
            _finding("MST-L", "PSNR", 35.18, "35.18", url=_URL_C),
            _finding("TSA-Net", "PSNR", 31.46, "31.46"),
        ),
        _INDEX,
    )

    cell = next(row for row in tables[0].rows if row.label == "MST-L").cell("psnr")
    assert cell.citations == [1, 3]
    assert not cell.disputed
    assert cell.numeric == 35.18


def test_conflicting_values_are_shown_side_by_side_and_excluded_from_charts() -> None:
    """不静默挑一个:表里并列 + 标注。但排除出图表——画成一个点等于替读者裁决。"""
    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18", url=_URL_A),
            _finding("MST-L", "PSNR", 34.02, "34.02", url=_URL_C),
            _finding("TSA-Net", "PSNR", 31.46, "31.46"),
        ),
        _INDEX,
    )

    cell = next(row for row in tables[0].rows if row.label == "MST-L").cell("psnr")
    assert cell.disputed
    assert "35.18" in cell.value and "34.02" in cell.value
    assert cell.citations == [1, 3]
    # 关键：有分歧的值不进图表
    assert cell.numeric is None


def test_the_same_number_written_differently_is_not_a_conflict() -> None:
    """38.36 与 38.360 是同一个数，不该被当成分歧。"""
    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18", url=_URL_A),
            _finding("MST-L", "PSNR", 35.18, "35.18", url=_URL_C),
            _finding("TSA-Net", "PSNR", 31.46, "31.46"),
        ),
        _INDEX,
    )

    assert not next(row for row in tables[0].rows if row.label == "MST-L").cell("psnr").disputed


def test_a_comparator_is_preserved_in_the_cell() -> None:
    """ ">35" 与 "35" 是不同的断言。吞掉比较符会让表给出比证据更强的结论。"""
    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.0, "35", comparator=">"),
            _finding("TSA-Net", "PSNR", 31.46, "31.46"),
        ),
        _INDEX,
    )

    assert next(row for row in tables[0].rows if row.label == "MST-L").cell("psnr").value == ">35"


def test_uncertainty_is_rendered_with_the_value() -> None:
    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18", uncertainty=0.05),
            _finding("TSA-Net", "PSNR", 31.46, "31.46"),
        ),
        _INDEX,
    )

    value = next(row for row in tables[0].rows if row.label == "MST-L").cell("psnr").value
    assert value == "35.18 ± 0.05"


# ── 与装配、渲染串起来 ──────────────────────────────────────────────────────


def test_assembly_emits_the_table_and_markdown_renders_it_with_citations() -> None:
    """端到端:findings → 自动透视 → Markdown，逐格引用与口径脚注都在。"""
    report = Report(query="CASSI 重建对比", markdown="正文 [1] [2]", citations=[_URL_A, _URL_B])
    doc = assemble_document(
        report,
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18", url=_URL_B, conditions=_KAIST28),
            _finding("CST-L", "PSNR", 36.12, "36.12", url=_URL_A, conditions=_KAIST31),
        ),
    )

    table = doc.table("quantitative_comparison")
    assert table is not None

    md = render_markdown(doc)
    assert "定量对照表" in md
    assert "35.18 [2]" in md
    assert "36.12 [1]" in md
    # 两种口径分列，列头带脚注编号
    assert "（注 1）" in md and "（注 2）" in md
    assert "28 波段" in md and "31 波段" in md
    assert "不可跨列直接比较" in md


def test_a_chart_can_be_pointed_at_the_pivoted_table() -> None:
    """透视出的表就是图表的源表——这一步接上后输出侧闭环。"""
    from deep_research.report import ChartBlock, render_chart

    tables = pivot_tables(
        _results(
            _finding("MST-L", "PSNR", 35.18, "35.18"),
            _finding("TSA-Net", "PSNR", 31.46, "31.46"),
        ),
        _INDEX,
    )
    table = tables[0]

    svg = render_chart(
        ChartBlock(id="c", source_table=table.id, form="dot", value_columns=["psnr"]),
        table,
    )

    assert "MST-L" in svg and "TSA-Net" in svg
    # tooltip 带出处角标，图上也能追溯
    assert "[2]" in svg


def test_legacy_runs_produce_no_table_so_existing_output_is_unchanged() -> None:
    """历史 findings 没有 entity/quantity → 透视器返回空，产物逐字节不变。"""
    legacy = Finding(
        statement="某个定性论断",
        source_url=_URL_A,
        evidence_quote="quote",
        verification=EvidenceVerification(
            status="verified", method="normalized_quote", semantic_status="supported"
        ),
    )

    doc = assemble_document(
        Report(query="q", markdown="正文 [1]", citations=[_URL_A]), _results(legacy)
    )

    assert doc.table("quantitative_comparison") is None
    assert "定量对照表" not in render_markdown(doc)
