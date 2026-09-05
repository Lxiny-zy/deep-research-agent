"""Optional server-side PDF export.

WeasyPrint is intentionally imported inside :func:`render_pdf`.  The API and
worker therefore remain usable on the normal lightweight installation; only a
request for this optional format requires the ``pdf`` extra.
"""

from __future__ import annotations

import re
from html import escape

from .document import ChartBlock, ProseBlock, ReportDocument, TableBlock


class PdfExportError(RuntimeError):
    """Base error for the optional PDF renderer."""


class PdfExportUnavailable(PdfExportError):
    """Raised when WeasyPrint is not installed in the current environment."""


class PdfRenderError(PdfExportError):
    """Raised when WeasyPrint cannot render a valid PDF."""


def render_pdf(document: ReportDocument) -> bytes:
    """Render a structured report to PDF using optional WeasyPrint."""

    try:
        from weasyprint import HTML
    except ImportError as exc:  # pragma: no cover - depends on deployment extras
        raise PdfExportUnavailable(
            "server-side PDF export requires the optional 'pdf' extra (weasyprint)"
        ) from exc

    try:
        return bytes(HTML(string=render_pdf_html(document), base_url=None).write_pdf())
    except Exception as exc:  # pragma: no cover - renderer/platform dependent
        raise PdfRenderError(f"server-side PDF rendering failed: {exc}") from exc


def render_pdf_html(document: ReportDocument) -> str:
    """Return the self-contained HTML consumed by WeasyPrint."""

    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<style>",
        "@page { size: A4; margin: 19mm 18mm 20mm; }",
        "@page :first { margin-top: 24mm; }",
        "* { box-sizing: border-box; }",
        "body { font-family: 'Noto Serif CJK SC', 'Source Han Serif SC', 'Noto Serif SC', "
        "'Noto Sans CJK SC', 'Microsoft YaHei', 'SimSun', serif; "
        "font-size: 10.5pt; line-height: 1.7; color: #1f2933; }",
        ".report-kicker { margin: 0 0 7pt; color: #66717d; font-family: 'Noto Sans CJK SC', "
        "'Microsoft YaHei', sans-serif; font-size: 8.5pt; letter-spacing: .12em; "
        "text-transform: uppercase; }",
        "h1 { max-width: 175mm; font-size: 23pt; line-height: 1.25; margin: 0 0 8pt; "
        "font-weight: 600; color: #16202a; }",
        "h2 { font-size: 15pt; line-height: 1.35; margin: 21pt 0 8pt; padding-bottom: 4pt; "
        "border-bottom: .7pt solid #c7cdd3; break-after: avoid; color: #23313d; }",
        "h3 { font-size: 11.5pt; line-height: 1.45; margin: 13pt 0 5pt; break-after: avoid; "
        "color: #2b3945; }",
        "p { margin: 0 0 8pt; }",
        ".report-meta { display: flex; gap: 12pt; flex-wrap: wrap; margin: 0 0 14pt; "
        "padding-bottom: 9pt; border-bottom: 1pt solid #aeb7c0; color: #66717d; "
        "font-family: 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif; font-size: 8.5pt; }",
        ".disclaimer { margin: 0 0 16pt; padding: 8pt 10pt; border-left: 2.5pt solid #7a8794; "
        "background: #f2f4f5; color: #52616d; font-family: 'Noto Sans CJK SC', "
        "'Microsoft YaHei', sans-serif; "
        "font-size: 9pt; line-height: 1.6; }",
        ".overview { break-inside: avoid; margin: 0 0 16pt; }",
        ".overview h2 { margin-top: 0; }",
        "table { width: 100%; border-collapse: collapse; margin: 8pt 0 13pt; break-inside: auto; "
        "font-family: 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif; font-size: 9.5pt; }",
        "thead { display: table-header-group; }",
        "tr { break-inside: avoid; }",
        "th, td { border-bottom: .6pt solid #c7cdd3; padding: 5pt 6pt; vertical-align: top; }",
        "th { border-top: 1pt solid #7f8b96; border-bottom: 1pt solid #7f8b96; "
        "background: #edf0f2; "
        "text-align: left; font-weight: 600; color: #33414d; }",
        ".overview table { width: auto; min-width: 68mm; }",
        ".overview td:last-child, .overview th:last-child { text-align: right; "
        "font-variant-numeric: tabular-nums; }",
        ".muted { color: #66717d; font-family: 'Noto Sans CJK SC', "
        "'Microsoft YaHei', sans-serif; }",
        ".evidence { border-left: 2pt solid #7a8794; padding: 3pt 0 4pt 9pt; margin: 7pt 0 12pt; "
        "break-inside: avoid; }",
        ".evidence h3 { margin-top: 0; }",
        ".hash { font-family: 'SFMono-Regular', Consolas, monospace; font-size: 7.5pt; "
        "overflow-wrap: anywhere; "
        "color: #66717d; }",
        ".audit { margin: 3pt 0; font-family: 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif; "
        "font-size: 9pt; }",
        ".table-notes { margin: 3pt 0 12pt 14pt; padding-left: 10pt; color: #52616d; "
        "font-size: 9pt; }",
        "blockquote { margin: 6pt 0; padding: 5pt 8pt; border-left: 1.5pt solid #b3bdc6; "
        "background: #f6f7f8; color: #465460; }",
        ".references { break-before: page; }",
        ".appendix { break-before: page; }",
        "ol { margin-top: 4pt; padding-left: 18pt; }",
        "li { margin: 0 0 4pt; }",
        "</style></head><body>",
    ]
    if document.query:
        parts.append("<p class='report-kicker'>Deep Research · Evidence Report</p>")
        parts.append(f"<h1>{escape(_display_title(document.query))}</h1>")
    parts.append(f"<p class='disclaimer'>{escape(document.disclaimer)}</p>")
    _append_overview(parts, document)
    for block in document.blocks:
        if isinstance(block, ProseBlock):
            _append_prose(parts, block.markdown)
        elif isinstance(block, TableBlock):
            _append_table(parts, block)
        elif isinstance(block, ChartBlock):
            parts.append(f"<h2>{escape(block.title or block.id)}</h2>")
            parts.append(f"<p class='muted'>Chart source table: {escape(block.source_table)}</p>")
    _append_references(parts, document)
    _append_evidence(parts, document)
    parts.append("</body></html>")
    return "".join(parts)


def _append_overview(parts: list[str], document: ReportDocument) -> None:
    overview = document.overview
    if not overview.records:
        return
    rows = [
        ("Evidence records", overview.records),
        ("Verbatim matched", overview.verbatim_matched),
        ("Semantically supported", overview.semantically_supported),
        ("Corroborated", overview.corroborated),
        ("Conflicted", overview.conflicted),
        (
            "Blocked sources",
            "unavailable" if overview.blocked_sources is None else overview.blocked_sources,
        ),
    ]
    parts.append(
        "<section class='overview'><h2>Evidence overview</h2>"
        "<table><thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>"
    )
    parts.extend(
        f"<tr><td>{escape(str(label))}</td><td>{escape(str(value))}</td></tr>"
        for label, value in rows
    )
    parts.append("</tbody></table></section>")


def _append_prose(parts: list[str], markdown: str) -> None:
    for paragraph in markdown.split("\n\n"):
        text = paragraph.strip()
        if not text:
            continue
        if text.startswith("### "):
            parts.append(f"<h3>{escape(text[4:])}</h3>")
        elif text.startswith("## "):
            parts.append(f"<h2>{escape(text[3:])}</h2>")
        elif text.startswith("# "):
            parts.append(f"<h1>{escape(text[2:])}</h1>")
        else:
            parts.append(f"<p>{escape(text).replace(chr(10), '<br>')}</p>")


def _append_table(parts: list[str], table: TableBlock) -> None:
    if table.title:
        parts.append(f"<h2>{escape(table.title)}</h2>")
    if not table.columns:
        return
    parts.append("<table><thead><tr><th>Entity</th>")
    parts.extend(f"<th>{escape(_column_header(column))}</th>" for column in table.columns)
    parts.append("</tr></thead><tbody>")
    for row in table.rows:
        label = row.label
        if row.citation:
            label += f" [{row.citation}]"
        parts.append(f"<tr><td>{escape(label)}</td>")
        for column in table.columns:
            cell = row.cell(column.key)
            parts.append(f"<td>{escape(_cell_value(cell))}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    if table.caption:
        parts.append(f"<p class='muted'>{escape(table.caption)}</p>")
    if table.notes:
        parts.append("<div class='table-notes'><strong>Protocol notes</strong><ol>")
        parts.extend(f"<li>{escape(note)}</li>" for note in table.notes)
        parts.append("</ol></div>")


def _column_header(column: object) -> str:
    """Keep column units and protocol-note markers visible in print output."""

    key = str(getattr(column, "key", "") or "")
    label = str(getattr(column, "label", "") or key).strip() or key
    unit = str(getattr(column, "unit", "") or "").strip()
    if unit:
        label = f"{label} ({unit})"
    note_ref = getattr(column, "note_ref", None)
    if note_ref is not None:
        label = f"{label} [note {note_ref}]"
    return label


def _cell_value(cell: object) -> str:
    """Render a cell without dropping provenance or disagreement markers."""

    reported = bool(getattr(cell, "reported", False))
    value = str(getattr(cell, "value", "") or "") if reported else "Not reported"
    citations = getattr(cell, "citations", ()) or ()
    if citations:
        value += " " + " ".join(f"[{citation}]" for citation in citations)
    note_ref = getattr(cell, "note_ref", None)
    if note_ref is not None:
        value += f" [note {note_ref}]"
    if bool(getattr(cell, "disputed", False)):
        value += " [disputed]"
    return value


def _append_references(parts: list[str], document: ReportDocument) -> None:
    if not document.references:
        return
    parts.append("<section class='references'><h2>References</h2><ol>")
    parts.extend(
        f"<li>[{reference.index}] {escape(reference.render())}</li>"
        for reference in document.references
    )
    parts.append("</ol></section>")


def _append_evidence(parts: list[str], document: ReportDocument) -> None:
    if not document.evidence:
        return
    parts.append("<section class='appendix'><h2>Evidence appendix</h2>")
    for record in document.evidence:
        parts.append("<section class='evidence'>")
        parts.append(f"<h3>[{record.citation}] {escape(record.statement)}</h3>")
        if record.context:
            parts.append(f"<blockquote>{escape(record.context)}</blockquote>")
        if record.quote:
            parts.append(f"<blockquote>{escape(record.quote)}</blockquote>")
        if record.quantity_label:
            _append_audit(parts, "Quantity", record.quantity_label)
        if record.conditions_label:
            _append_audit(parts, "Conditions", record.conditions_label)
        if record.quantity_status != "not_applicable":
            parts.append(
                "<p class='audit'><strong>Quantity check:</strong> "
                f"{escape(record.quantity_status)}"
                + (f" ({escape(record.quantity_reason)})" if record.quantity_reason else "")
                + "</p>"
            )
        if record.source_section:
            parts.append(f"<p>Source section: {escape(record.source_section)}</p>")
        if record.verification_reason:
            _append_audit(parts, "Verification reason", record.verification_reason)
        if record.semantic_status != "not_checked":
            semantic = f"{record.semantic_status} ({record.semantic_confidence:.0%})"
            _append_audit(parts, "Semantic check", semantic)
        if record.semantic_reason:
            _append_audit(parts, "Semantic reason", record.semantic_reason)
        if record.consistency_status != "not_checked":
            _append_audit(parts, "Consistency", record.consistency_status)
        if record.contradiction_reason:
            _append_audit(parts, "Contradiction reason", record.contradiction_reason)
        if record.corroboration_status != "not_checked":
            corroboration = (
                f"{record.corroboration_status}; "
                f"{record.independent_source_count} independent source(s)"
            )
            _append_audit(parts, "Corroboration", corroboration)
        if record.corroboration_reason:
            _append_audit(parts, "Corroboration reason", record.corroboration_reason)
        if record.contradicts_claim_ids:
            ids = ", ".join(record.contradicts_claim_ids)
            _append_audit(parts, "Contradicts", ids)
        if record.content_hash:
            parts.append(f"<p class='hash'>Snapshot hash: {escape(record.content_hash)}</p>")
        if record.source_url:
            parts.append(f"<p>Source: {escape(record.source_url)}</p>")
        parts.append("</section>")
    parts.append("</section>")


def _display_title(value: str) -> str:
    """Remove a Markdown heading marker accidentally included in a run query."""

    return re.sub(r"^\s*#{1,6}\s+", "", value).strip() or "Research report"


def _append_audit(parts: list[str], label: str, value: str) -> None:
    parts.append(f"<p class='audit'><strong>{escape(label)}:</strong> {escape(value)}</p>")


__all__ = [
    "PdfExportError",
    "PdfExportUnavailable",
    "PdfRenderError",
    "render_pdf",
    "render_pdf_html",
]
