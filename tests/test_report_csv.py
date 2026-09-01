"""CSV projection tests for structured report tables."""

from __future__ import annotations

import csv
import io

import pytest

from deep_research.report import (
    CsvTableNotFoundError,
    CsvTableSelectionError,
    ProseBlock,
    ReferenceEntry,
    ReportDocument,
    TableBlock,
    TableCell,
    TableColumn,
    TableRow,
    render_csv,
)


def _table(table_id: str = "benchmark") -> TableBlock:
    return TableBlock(
        id=table_id,
        title="Benchmark",
        columns=[
            TableColumn(key="psnr", label="PSNR", unit="dB", numeric=True, note_ref=1),
            TableColumn(key="ssim", label="SSIM", numeric=True),
        ],
        rows=[
            TableRow(
                label="MST-L",
                citation=1,
                cells={
                    "psnr": TableCell(value="38.36", numeric=38.36, citations=[1], note_ref=1),
                    "ssim": TableCell(),
                },
            ),
            TableRow(
                label="TSA-Net",
                cells={
                    "psnr": TableCell(value="38.40 / 37.90", citations=[1, 2], disputed=True),
                    "ssim": TableCell(value="0.95", numeric=0.95, citations=[2]),
                },
            ),
        ],
        notes=["KAIST, 28 bands"],
        caption="Values are reported by the cited sources.",
    )


def test_render_csv_preserves_values_provenance_and_notes() -> None:
    document = ReportDocument(
        query="benchmark",
        blocks=[ProseBlock(markdown="prose"), _table()],
        references=[
            ReferenceEntry(index=1, url="https://example.test/a", reference="Paper A"),
            ReferenceEntry(index=2, url="https://example.test/b"),
        ],
    )

    rows = list(csv.reader(io.StringIO(render_csv(document))))

    assert rows[0] == ["对象", "PSNR (dB) [注 1]", "SSIM"]
    assert rows[1] == ["MST-L [1]", "38.36 [1] [注 1]", "未报告"]
    assert rows[2] == ["TSA-Net", "38.40 / 37.90 [1] [2] ⚠", "0.95 [2]"]
    assert ["口径脚注", "", ""] in rows
    assert ["[1] KAIST, 28 bands", "", ""] in rows
    assert ["说明: Values are reported by the cited sources.", "", ""] in rows
    assert ["参考来源", "", ""] in rows
    assert ["[1] Paper A", "", ""] in rows
    assert ["[2] https://example.test/b", "", ""] in rows


def test_render_csv_returns_empty_for_a_document_without_tables() -> None:
    assert render_csv(ReportDocument(query="q", blocks=[ProseBlock(markdown="text")])) == ""


def test_render_csv_neutralizes_formula_like_untrusted_text() -> None:
    document = ReportDocument(
        blocks=[
            TableBlock(
                id="unsafe",
                title="Unsafe",
                columns=[TableColumn(key="value", label="@header", unit="=unit")],
                rows=[
                    TableRow(
                        label="+row",
                        cells={"value": TableCell(value="  =1+1")},
                    )
                ],
                notes=["-note"],
                caption="@caption",
            )
        ],
        references=[ReferenceEntry(index=1, url="https://example.test", reference="=ref")],
    )

    rows = list(csv.reader(io.StringIO(render_csv(document))))

    assert rows[0][1].startswith("'@header")
    assert rows[1] == ["'+row", "'  =1+1"]
    assert ["[1] -note", ""] in rows
    assert ["说明: @caption", ""] in rows
    assert ["[1] =ref", ""] in rows


def test_render_csv_requires_a_table_id_for_multiple_tables() -> None:
    document = ReportDocument(blocks=[_table("a"), _table("b")])

    with pytest.raises(CsvTableSelectionError, match="table_id is required"):
        render_csv(document)

    assert render_csv(document, table_id="b").startswith("对象,")
    with pytest.raises(CsvTableNotFoundError, match="table not found"):
        render_csv(document, table_id="missing")
