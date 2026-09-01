"""XLSX projection tests for structured report tables."""

from __future__ import annotations

import io

import pytest

from deep_research.report import (
    ProseBlock,
    ReferenceEntry,
    ReportDocument,
    TableBlock,
    TableCell,
    TableColumn,
    TableRow,
    XlsxDependencyError,
    XlsxTableNotFoundError,
    XlsxTableSelectionError,
    render_xlsx,
)
from deep_research.report.csv import _cell_value, _column_header


def _table(table_id: str = "benchmark") -> TableBlock:
    return TableBlock(
        id=table_id,
        title="Benchmark / Results",
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
                    "psnr": TableCell(value="=38.40", citations=[1, 2], disputed=True),
                    "ssim": TableCell(value="0.95", numeric=0.95, citations=[2]),
                },
            ),
        ],
        notes=["KAIST, 28 bands"],
        caption="Values are reported by the cited sources.",
    )


def _open_workbook(payload: bytes):
    openpyxl = pytest.importorskip("openpyxl")
    return openpyxl.load_workbook(io.BytesIO(payload), data_only=False)


def test_render_xlsx_preserves_values_provenance_and_notes() -> None:
    document = ReportDocument(
        query="benchmark",
        blocks=[ProseBlock(markdown="prose"), _table()],
        references=[
            ReferenceEntry(index=1, url="https://example.test/a", reference="Paper A"),
            ReferenceEntry(index=2, url="https://example.test/b"),
        ],
    )

    workbook = _open_workbook(render_xlsx(document))
    sheet = workbook["Benchmark _ Results"]
    assert [sheet.cell(1, column).value for column in range(1, 3)] == [
        "对象",
        _column_header(_table().columns[0]),
    ]
    assert sheet.cell(2, 1).value == "MST-L [1]"
    assert sheet.cell(2, 2).value == _cell_value(_table().rows[0].cell("psnr"))
    assert sheet.cell(2, 3).value == _cell_value(_table().rows[0].cell("ssim"))
    # Formula-looking source text is forced to a string cell.
    assert sheet.cell(3, 2).value == _cell_value(_table().rows[1].cell("psnr"))
    assert sheet.cell(3, 2).data_type == "s"
    values = [cell.value for row in sheet.iter_rows() for cell in row]
    assert "口径脚注" in values
    assert "[1] KAIST, 28 bands" in values
    assert "说明" in values
    assert "参考来源" in values
    assert "[1] Paper A" in values


def test_render_xlsx_returns_openable_empty_workbook_without_tables() -> None:
    workbook = _open_workbook(render_xlsx(ReportDocument(blocks=[ProseBlock(markdown="text")])))
    assert workbook.active["A1"].value == "暂无表格"


def test_render_xlsx_requires_a_table_id_for_multiple_tables() -> None:
    document = ReportDocument(blocks=[_table("a"), _table("b")])

    with pytest.raises(XlsxTableSelectionError, match="table_id is required"):
        render_xlsx(document)
    assert render_xlsx(document, table_id="b")
    with pytest.raises(XlsxTableNotFoundError, match="table not found"):
        render_xlsx(document, table_id="missing")


def test_render_xlsx_exposes_a_clear_optional_dependency_error(monkeypatch) -> None:
    import deep_research.report.xlsx as xlsx

    monkeypatch.setattr(
        xlsx,
        "_load_openpyxl",
        lambda: (_ for _ in ()).throw(XlsxDependencyError("install the xlsx extra")),
    )
    with pytest.raises(XlsxDependencyError, match="xlsx extra"):
        render_xlsx(ReportDocument(blocks=[_table()]))
