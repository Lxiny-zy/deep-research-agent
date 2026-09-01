"""CSV projection for structured report tables.

CSV is intentionally a projection of one ``TableBlock`` rather than a second
table-building path.  The table values, missing-value marker, provenance
citations, and protocol notes therefore stay aligned with the Markdown
renderer and with the evidence apparatus in ``ReportDocument``.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from .document import ReportDocument, TableBlock, TableCell, TableColumn


class CsvExportError(ValueError):
    """Base error for an invalid structured-table CSV request."""


class CsvTableNotFoundError(CsvExportError):
    """Raised when a requested table id is not present in the document."""


class CsvTableSelectionError(CsvExportError):
    """Raised when a document contains multiple tables without a selection."""


def render_csv(
    document: ReportDocument,
    *,
    table_id: str | None = None,
    include_references: bool = True,
) -> str:
    """Render one structured report table as UTF-8 CSV text.

    A report may eventually contain several domain-specific tables with
    different schemas.  Silently concatenating those schemas into one CSV
    would produce a file that looks tabular but is not useful for review, so
    callers must select ``table_id`` when more than one table is present.
    A document with no table returns an empty string, which keeps the export
    endpoint usable while a run is still streaming or before a report exists.

    Cell citations remain inline (``[n]``) and the optional trailing sections
    preserve the corresponding source references and protocol notes.  Values
    are not Markdown-escaped or numerically coerced.  Formula-like text is
    prefixed with an apostrophe so spreadsheet applications keep untrusted
    model and source content as data.
    """

    tables = [block for block in document.blocks if isinstance(block, TableBlock)]
    table = _select_table(tables, table_id)
    if table is None or not table.columns:
        return ""

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    headers = ["对象", *(_column_header(column) for column in table.columns)]
    _write_row(writer, headers)

    for row in table.rows:
        label = row.label
        if row.citation:
            label = f"{label} [{row.citation}]"
        _write_row(
            writer,
            [label, *(_cell_value(row.cell(column.key)) for column in table.columns)],
        )

    width = len(headers)
    if table.notes:
        _write_row(writer, [])
        _write_row(writer, ["口径脚注", *([""] * (width - 1))])
        for index, note in enumerate(table.notes, 1):
            _write_row(writer, [f"[{index}] {note}", *([""] * (width - 1))])

    if table.caption:
        _write_row(writer, [])
        _write_row(writer, [f"说明: {table.caption}", *([""] * (width - 1))])

    if include_references and document.references:
        _write_row(writer, [])
        _write_row(writer, ["参考来源", *([""] * (width - 1))])
        for reference in document.references:
            _write_row(
                writer,
                [f"[{reference.index}] {reference.render()}", *([""] * (width - 1))],
            )

    return output.getvalue()


def _write_row(writer: Any, values: list[str]) -> None:
    writer.writerow([_spreadsheet_safe(value) for value in values])


def _spreadsheet_safe(value: str) -> str:
    text = str(value)
    candidate = text.lstrip()
    if candidate.startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return f"'{text}"
    return text


def _select_table(tables: list[TableBlock], table_id: str | None) -> TableBlock | None:
    if table_id:
        for table in tables:
            if table.id == table_id:
                return table
        raise CsvTableNotFoundError(f"table not found: {table_id}")
    if len(tables) > 1:
        ids = ", ".join(table.id for table in tables)
        raise CsvTableSelectionError(f"table_id is required when multiple tables exist: {ids}")
    return tables[0] if tables else None


def _column_header(column: TableColumn) -> str:
    label = column.label.strip() or column.key
    if column.unit.strip():
        label = f"{label} ({column.unit.strip()})"
    if column.note_ref is not None:
        label = f"{label} [注 {column.note_ref}]"
    return label


def _cell_value(cell: TableCell) -> str:
    if not cell.reported:
        return "未报告"

    value = cell.value
    if cell.citations:
        value += " " + " ".join(f"[{citation}]" for citation in cell.citations)
    if cell.note_ref is not None:
        value += f" [注 {cell.note_ref}]"
    if cell.disputed:
        value += " ⚠"
    return value
