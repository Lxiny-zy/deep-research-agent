from __future__ import annotations

import sys

import pytest

from deep_research.report import (
    EvidenceRecord,
    PdfExportUnavailable,
    ProseBlock,
    ReferenceEntry,
    ReportDocument,
    TableBlock,
    TableCell,
    TableColumn,
    TableRow,
    render_pdf,
    render_pdf_html,
)


def _document() -> ReportDocument:
    return ReportDocument(
        query="CASSI 方法比较",
        blocks=[
            ProseBlock(markdown="## 结论\n\nMST-L 达到 38.36 dB [1]。"),
            TableBlock(
                id="benchmark",
                title="Benchmark",
                columns=[
                    TableColumn(
                        key="psnr",
                        label="PSNR",
                        unit="dB",
                        numeric=True,
                        note_ref=1,
                    )
                ],
                rows=[
                    TableRow(
                        label="MST-L",
                        citation=1,
                        cells={
                            "psnr": TableCell(
                                value="38.36",
                                numeric=38.36,
                                citations=[1],
                                note_ref=1,
                                disputed=True,
                            )
                        },
                    )
                ],
                notes=["Values use the reported 28-band protocol."],
                caption="Comparable evaluation protocol",
            ),
        ],
        references=[ReferenceEntry(index=1, url="https://example.test/paper")],
        evidence=[
            EvidenceRecord(
                citation=1,
                statement="MST-L 达到 38.36 dB",
                quote="38.36 dB",
                context="The method reaches 38.36 dB in the Results section.",
                source_section="results",
                content_hash="ab" * 32,
                source_url="https://example.test/paper",
                quantity_label="PSNR = 38.36 dB",
                conditions_label="KAIST; 10 scenes; 28 bands",
                quantity_status="verified",
                quantity_reason="quantity_found_in_evidence",
                verification_reason="quote_found_in_source",
                semantic_status="supported",
                semantic_confidence=0.95,
                semantic_reason="metric and value agree",
                consistency_status="clear",
                corroboration_status="corroborated",
                independent_source_count=2,
                corroboration_reason="independent source agrees",
            )
        ],
    )


def test_pdf_html_contains_structured_tables_references_and_evidence() -> None:
    html = render_pdf_html(_document())
    assert "<meta charset='utf-8'>" in html
    assert "CASSI 方法比较" in html
    assert "MST-L" in html and "38.36 [1]" in html
    assert "PSNR (dB) [note 1]" in html
    assert "38.36 [1] [note 1] [disputed]" in html
    assert "Protocol notes" in html
    assert "Values use the reported 28-band protocol." in html
    assert "Quantity:</strong> PSNR = 38.36 dB" in html
    assert "Conditions:</strong> KAIST; 10 scenes; 28 bands" in html
    assert "Corroboration:</strong> corroborated; 2 independent source(s)" in html
    assert "Evidence appendix" in html
    assert "ab" * 32 in html
    assert "Noto Sans CJK SC" in html


def test_pdf_title_drops_a_markdown_heading_marker_from_the_query() -> None:
    document = _document().model_copy(update={"query": "## 调研一下 2026"})

    html = render_pdf_html(document)

    assert "<h1>调研一下 2026</h1>" in html
    assert "<h1>## 调研一下 2026</h1>" not in html


def test_pdf_dependency_is_lazy_and_reports_a_clear_error(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "weasyprint", None)
    with pytest.raises(PdfExportUnavailable, match="optional 'pdf' extra"):
        render_pdf(_document())


def _prose_html(markdown: str) -> str:
    document = _document().model_copy(update={"blocks": [ProseBlock(markdown=markdown)]})
    return render_pdf_html(document)


def test_prose_markdown_table_becomes_a_real_table() -> None:
    """The browser preview renders body tables via remark-gfm; so must the PDF."""
    html = _prose_html(
        "| 分类维度 | DOE 类型 | 核心作用 |\n"
        "|---|---|---|\n"
        "| 基础光束控制 | 顶帽光束整形 | 调整光强分布[6] |\n"
        "| 多波长光场 | 双波长 DOE | 支持更宽光谱范围[3][6] |\n"
    )

    assert "<th>分类维度</th>" in html
    assert "<td>顶帽光束整形</td>" in html
    assert "<td>支持更宽光谱范围[3][6]</td>" in html
    # The raw pipe syntax must not survive anywhere in the body.
    assert "| 分类维度 |" not in html
    assert "|---|" not in html


def test_prose_table_honours_alignment_and_ragged_rows() -> None:
    html = _prose_html(
        "| Metric | Value |\n"
        "| :--- | ---: |\n"
        "| PSNR | 38.36 |\n"
        "| SSIM |\n"  # short row: the missing cell is padded, not dropped
    )

    assert "<th style='text-align: right'>Value</th>" in html
    # ``:---`` is the default alignment, so it emits no style attribute.
    assert "<td>PSNR</td>" in html
    assert html.count("<tr>") == 3  # header plus two body rows
    assert "<td style='text-align: right'></td>" in html  # padded short row


def test_prose_table_keeps_escaped_pipes_inside_a_cell() -> None:
    html = _prose_html("| Expr | Note |\n|---|---|\n| a \\| b | union |\n")

    assert "<td>a | b</td>" in html
    assert "<td>union</td>" in html


def test_prose_renders_lists_quotes_code_and_rules() -> None:
    html = _prose_html(
        "- 第一项\n"
        "- 第二项\n"
        "\n"
        "3. 起始于三\n"
        "4. 第四\n"
        "\n"
        "> 引用一行\n"
        "\n"
        "---\n"
        "\n"
        "```python\nx = 1 < 2\n```\n"
    )

    assert "<ul><li>第一项</li><li>第二项</li></ul>" in html
    assert "<ol start='3'><li>起始于三</li><li>第四</li></ol>" in html
    assert "<blockquote><p>引用一行</p></blockquote>" in html
    assert "<hr>" in html
    assert "<pre><code>x = 1 &lt; 2</code></pre>" in html
    # None of the source markers should leak through as text.
    assert "- 第一项" not in html
    assert "```python" not in html


def test_prose_renders_inline_emphasis_and_leaves_citations_alone() -> None:
    html = _prose_html("**关键结论**：*强调* 与 `code_span` 并存，见 [3][6]。")

    assert "<strong>关键结论</strong>" in html
    assert "<em>强调</em>" in html
    assert "<code>code_span</code>" in html
    assert "[3][6]" in html  # citation markers stay literal for the reference list
    assert "**关键结论**" not in html


def test_prose_does_not_treat_emphasis_inside_a_code_span() -> None:
    html = _prose_html("Use `a * b * c` verbatim.")

    assert "<code>a * b * c</code>" in html
    assert "<em>" not in html.split("Evidence appendix")[0]


def test_prose_escapes_html_and_deep_headings_collapse_to_h3() -> None:
    html = _prose_html("#### 四级标题\n\n<script>alert(1)</script>\n")

    assert "<h3>四级标题</h3>" in html
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_prose_keeps_a_pipe_paragraph_without_a_delimiter_row_as_text() -> None:
    """A lone pipe line is not a table; turning it into one would invent columns."""
    html = _prose_html("Values are a | b | c in the raw log.\n")

    assert "<p>Values are a | b | c in the raw log.</p>" in html
