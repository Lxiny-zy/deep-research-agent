"""从已落库的运行数据装配 ``ReportDocument``。

这套 join（引用号 → URL → findings → 徽章）原先只存在于前端 TypeScript，因此
.md 导出会丢掉整个证据装置。移到 Python 侧后它成为唯一的规范化装配路径，所以
测试重点是"装配结果与既有前端口径一致，且旧数据不会被编造补全"：

* ``Report.markdown``/``citations`` 契约不变，只读不改；
* 参考来源顺序严格跟随 ``citations``——前端按下标跳转，错位就是引错来源；
* 引用文本只取落库的 ``source_reference``，不重新推导（保证回放一致）；
* 概览统计口径与前端 ``summarizeEvidence`` 一致；
* 拿不到审计事件时拦截数是"不可用"而不是 0。
"""

from __future__ import annotations

import pytest

from deep_research.models import (
    EvidenceVerification,
    Finding,
    Report,
    ResearchResult,
)
from deep_research.observability import Event
from deep_research.report import (
    ChartBlock,
    ProseBlock,
    TableBlock,
    TableCell,
    TableColumn,
    TableRow,
    assemble_document,
    render_markdown,
)


def _finding(
    statement: str,
    url: str,
    *,
    reference: str = "",
    quote: str = "q",
    verified: bool = True,
    semantic: str = "supported",
    consistency: str = "clear",
    corroboration: str = "corroborated",
    sources: int = 2,
    claim_id: str = "",
    contradicts: list[str] | None = None,
) -> Finding:
    return Finding(
        statement=statement,
        source_url=url,
        evidence_quote=quote,
        verification=EvidenceVerification(
            status="verified" if verified else "unverified",
            method="normalized_quote" if verified else "none",
            source_content_hash="ab" * 32,
            source_title="标题",
            source_reference=reference,
            evidence_context=f"上下文包含 {quote} 的一段原文。",
            reason="quote_found_in_source",
            semantic_status=semantic,  # type: ignore[arg-type]
            semantic_confidence=0.9,
            semantic_reason="原文支持该论断",
            claim_id=claim_id,
            consistency_status=consistency,  # type: ignore[arg-type]
            contradicts_claim_ids=contradicts or [],
            corroboration_status=corroboration,  # type: ignore[arg-type]
            independent_source_count=sources,
            corroborates_claim_ids=["C-9"] if corroboration == "corroborated" else [],
            corroboration_reason="两个独立发布方支持",
        ),
    )


_DOI_A = "https://doi.org/10.1364/oe.1"
_DOI_B = "https://arxiv.org/abs/2205.10102v3"


def _run() -> tuple[Report, list[ResearchResult]]:
    report = Report(
        query="CASSI 重建方法对比",
        markdown=(
            "# 报告\n\n正文引用了 [1] 与 [2]。\n\n"
            "## 参考来源\n[1] https://doi.org/10.1364/oe.1\n[2] https://arxiv.org/abs/2205.10102v3\n"
        ),
        citations=[_DOI_A, _DOI_B],
    )
    results = [
        ResearchResult(
            sub_question="重建精度如何",
            findings=[
                _finding(
                    "该方法达到 38.36 dB",
                    _DOI_B,
                    reference="Cai 等. DAUHST. NeurIPS, 2022. https://arxiv.org/abs/2205.10102v3",
                    claim_id="C-1",
                ),
                _finding(
                    "编码孔径为单色散结构",
                    _DOI_A,
                    reference="Wagadarikar 等. SD-CASSI. Optics Express, 2008. "
                    "https://doi.org/10.1364/oe.1",
                    claim_id="C-2",
                ),
            ],
        )
    ]
    return report, results


# ── 契约不变 ────────────────────────────────────────────────────────────────


def test_assembly_does_not_mutate_the_report_contract() -> None:
    """Report 是只读输入:前端 [n] 跳转与快照覆盖率指标都还依赖它的原样。"""
    report, results = _run()
    before = report.model_dump()

    assemble_document(report, results)

    assert report.model_dump() == before


def test_references_follow_the_citation_order_exactly() -> None:
    """错位就是引错来源——前端是按下标取 URL 的。"""
    report, results = _run()

    doc = assemble_document(report, results)

    assert [(e.index, e.url) for e in doc.references] == [(1, _DOI_A), (2, _DOI_B)]


def test_reference_text_comes_from_the_persisted_render_not_a_fresh_derivation() -> None:
    """引用在验证时刻渲染并落库,这里只查表——否则回放与 worker 执行会不一致。"""
    report, results = _run()

    doc = assemble_document(report, results)

    assert doc.references[0].render().startswith("Wagadarikar 等. SD-CASSI.")
    assert doc.references[1].render().startswith("Cai 等. DAUHST.")


def test_a_source_without_scholarly_metadata_falls_back_to_the_bare_url() -> None:
    """历史 run 没有 source_reference,必须回退而不是渲染成空行。"""
    report = Report(query="q", markdown="正文 [1]", citations=["https://blog.test/p"])
    results = [
        ResearchResult(
            sub_question="sq", findings=[_finding("论断", "https://blog.test/p", reference="")]
        )
    ]

    doc = assemble_document(report, results)

    assert doc.references[0].render() == "https://blog.test/p"


# ── 正文去重 ────────────────────────────────────────────────────────────────


def test_body_drops_the_auto_appended_reference_section() -> None:
    """参考来源已是结构化字段;正文若带着那一段会渲染两遍。"""
    report, results = _run()

    doc = assemble_document(report, results)

    prose = next(b for b in doc.blocks if isinstance(b, ProseBlock))
    assert "## 参考来源" not in prose.markdown
    assert "正文引用了 [1] 与 [2]。" in prose.markdown


def test_body_keeps_a_mid_document_mention_of_references() -> None:
    """只在结尾匹配:正文中间讨论"参考来源"的段落不该被误删。"""
    report = Report(
        query="q",
        markdown="## 参考来源的可靠性\n\n本节讨论来源质量。\n\n## 结论\n\n完。",
        citations=[],
    )

    doc = assemble_document(report, [])

    prose = next(b for b in doc.blocks if isinstance(b, ProseBlock))
    assert "## 参考来源的可靠性" in prose.markdown
    assert "本节讨论来源质量。" in prose.markdown


# ── 证据附录 ────────────────────────────────────────────────────────────────


def test_evidence_is_grouped_by_citation_in_ascending_order() -> None:
    report, results = _run()

    doc = assemble_document(report, results)

    assert [record.citation for record in doc.evidence] == [1, 2]
    assert doc.evidence[0].statement == "编码孔径为单色散结构"


def test_evidence_excludes_findings_that_never_got_cited() -> None:
    """未被引用的 finding 在正文里没有角标,放进附录只是一堆无处可去的记录。

    但它们仍然计入概览——"验证了多少条"与"报告引用了哪些"是两个不同的量。
    """
    report = Report(query="q", markdown="正文 [1]", citations=[_DOI_A])
    results = [
        ResearchResult(
            sub_question="sq",
            findings=[
                _finding("被引用的", _DOI_A),
                _finding("未被引用的", "https://elsewhere.test/x"),
            ],
        )
    ]

    doc = assemble_document(report, results)

    assert [r.statement for r in doc.evidence] == ["被引用的"]
    assert doc.overview.records == 2


def test_evidence_record_carries_the_fields_that_were_tooltip_only() -> None:
    report, results = _run()

    doc = assemble_document(report, results)
    record = doc.evidence[0]

    assert record.verification_reason == "quote_found_in_source"
    assert record.semantic_reason == "原文支持该论断"
    assert record.corroboration_reason == "两个独立发布方支持"
    assert record.content_hash == "ab" * 32
    assert record.independent_source_count == 2


# ── 概览统计 ────────────────────────────────────────────────────────────────


def test_overview_matches_the_frontend_summary_semantics() -> None:
    """语义不支持的原文匹配不得计为语义支持——与前端 summarizeEvidence 同口径。"""
    results = [
        ResearchResult(
            sub_question="sq",
            findings=[
                _finding("a", _DOI_A),
                _finding("b", _DOI_A, semantic="unsupported"),
                _finding("c", _DOI_A, verified=False, semantic="not_checked"),
                _finding("d", _DOI_A, consistency="conflicted", corroboration="disputed"),
            ],
        )
    ]

    doc = assemble_document(None, results)

    o = doc.overview
    assert o.records == 4
    assert o.verbatim_matched == 3
    assert o.semantically_supported == 2
    assert o.corroborated == 3
    assert o.conflicted == 1


def test_blocked_sources_sums_the_source_policy_audit_events() -> None:
    events = [
        Event(stage="RESEARCHER", type="info", data={"category": "source_policy", "blocked": 2}),
        Event(stage="RESEARCHER", type="info", data={"category": "source_policy", "blocked": 1}),
        Event(stage="PLANNER", type="info", data={"category": "other", "blocked": 99}),
    ]

    doc = assemble_document(None, [], events=events)

    assert doc.overview.blocked_sources == 3


def test_blocked_sources_is_unavailable_rather_than_zero_without_audit_events() -> None:
    """旧 run 与关闭审计的部署本就拿不到这个数;写 0 是我们并不掌握的断言。"""
    assert assemble_document(None, [], events=[]).overview.blocked_sources is None
    assert assemble_document(None, []).overview.blocked_sources is None
    assert (
        assemble_document(
            None, [], events=[Event(stage="PLANNER", type="info", data={"category": "x"})]
        ).overview.blocked_sources
        is None
    )


# ── 与渲染器串起来 ──────────────────────────────────────────────────────────


def test_assembled_document_renders_to_markdown_with_the_full_apparatus() -> None:
    """端到端:落库数据 → 装配 → Markdown,证据装置全程不丢。"""
    report, results = _run()
    events = [
        Event(stage="RESEARCHER", type="info", data={"category": "source_policy", "blocked": 1})
    ]

    md = render_markdown(assemble_document(report, results, events=events))

    assert "正文引用了 [1] 与 [2]。" in md
    assert "## 证据链概览" in md
    assert "| 来源被拦截 | 1 |" in md
    assert "## 参考来源" in md
    assert "[2] Cai 等. DAUHST. NeurIPS, 2022." in md
    assert "## 证据附录" in md
    assert "该方法达到 38.36 dB" in md
    assert "ab" * 32 in md  # 完整快照哈希
    # 参考来源段落只出现一次（正文里那份已被剥掉）
    assert md.count("## 参考来源") == 1


def test_extra_blocks_let_tables_and_charts_join_without_changing_assembly() -> None:
    """块模型已就位:表格/图表阶段只需注入 extra_blocks,装配逻辑不用再改。"""
    table = TableBlock(
        id="t",
        title="重建算法对比",
        columns=[TableColumn(key="psnr", label="PSNR", unit="dB", numeric=True)],
        rows=[
            TableRow(
                label="DAUHST-9stg",
                citation=2,
                cells={"psnr": TableCell(value="38.36", numeric=38.36, citations=[2])},
            )
        ],
    )
    chart = ChartBlock(id="c", source_table="t", form="bar", value_columns=["psnr"], title="PSNR")
    report, results = _run()

    doc = assemble_document(report, results, extra_blocks=[chart, table])
    md = render_markdown(doc)

    assert doc.table("t") is not None
    assert "## PSNR" in md
    assert "见表《重建算法对比》" in md
    assert "38.36 [2]" in md


def test_assembly_tolerates_a_run_with_no_report_yet() -> None:
    """流式中途/失败的 run 也要能装配,否则打印预览会在这些状态下崩。"""
    doc = assemble_document(None, [])

    assert doc.blocks == []
    assert doc.references == []
    assert doc.evidence == []
    assert render_markdown(doc).strip()


@pytest.mark.parametrize("markdown", ["", "   \n\n  "])
def test_empty_report_body_produces_no_prose_block(markdown: str) -> None:
    doc = assemble_document(Report(query="q", markdown=markdown, citations=[]), [])

    assert not any(isinstance(block, ProseBlock) for block in doc.blocks)
