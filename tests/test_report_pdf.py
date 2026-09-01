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


def test_pdf_dependency_is_lazy_and_reports_a_clear_error(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "weasyprint", None)
    with pytest.raises(PdfExportUnavailable, match="optional 'pdf' extra"):
        render_pdf(_document())
