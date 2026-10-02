"""Row-preserving pagination inputs for renderer-owned HTML tables."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from html import unescape


def plain_html(text: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", text))


@dataclass
class PdfTable:
    header: str
    rows: list[str]
    caption: str = ""
    lead: str = ""

    def html(self, rows: list[str], *, continued: bool = False) -> str:
        caption = self.caption
        if continued:
            caption = (
                (caption[:-4] + "（续）</p>") if caption else '<p class="caption">表格（续）</p>'
            )
        return (
            ("" if continued else self.lead)
            + caption
            + '<table id="dr-table"><thead>'
            + self.header
            + "</thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )


def table_from_html(html: str, width: float) -> PdfTable:
    head = re.search(r"<thead>(.*?)</thead>", html, re.S)
    body = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
    if head is None or body is None:
        raise ValueError("table has no renderer-owned header/body")
    rows = re.findall(r"<tr>.*?</tr>", body[1], re.S)
    all_rows = [head[1], *rows]
    cells = [re.findall(r"<(?:th|td)>(.*?)</(?:th|td)>", row, re.S) for row in all_rows]
    columns = len(cells[0])
    if not columns or any(len(row) != columns for row in cells):
        raise ValueError("table cells do not match the header")
    # Compute widths from ALL rows, once. Per-page auto sizing would move the
    # same column when its longest value happens to be on another page.
    weights = [
        max(
            8,
            min(
                40,
                max(
                    sum(
                        2 if unicodedata.east_asian_width(c) in {"W", "F"} else 1
                        for c in plain_html(row[i])
                    )
                    for row in cells
                ),
            ),
        )
        for i in range(columns)
    ]

    # Formula images cannot wrap like text. Reserve their intrinsic width
    # before distributing the remaining space according to prose length.
    minimums = [
        max(
            [12.0]
            + [
                float(value) + 1
                for row in cells
                for value in re.findall(r'<img\b[^>]*\bwidth="([\d.]+)"', row[i])
            ]
        )
        for i in range(columns)
    ]
    available = max(1, width - columns * 10)
    if sum(minimums) > available:
        raise ValueError("表格公式总宽度超过版心，请将公式拆为多行或移到表格外")
    widths = [weight / sum(weights) * available for weight in weights]
    pending = set(range(columns))
    remaining = available
    while pending:
        total = sum(weights[i] for i in pending)
        constrained = {i for i in pending if weights[i] / total * remaining < minimums[i]}
        if not constrained:
            for i in pending:
                widths[i] = weights[i] / total * remaining
            break
        for i in constrained:
            widths[i] = minimums[i]
            remaining -= minimums[i]
        pending -= constrained

    def fixed_width(row: str, row_index: int) -> str:
        index = 0

        def cell(match: re.Match[str]) -> str:
            nonlocal index
            cell_width = widths[index]
            # Story lowers inline formula images without extending the cell
            # for their descent. Keep denominators clear of the bottom rule.
            descent = max(
                [0.0]
                + [
                    float(value)
                    for value in re.findall(r"vertical-align:-([\d.]+)pt", cells[row_index][index])
                ]
            )
            padding = f";padding-bottom:{4 + descent:.4f}pt" if descent else ""
            identity = f"dr-cell-{row_index}-{index}"
            index += 1
            return f'<{match[1]} id="{identity}" style="width:{cell_width:.4f}pt{padding}">'

        return re.sub(r"<(th|td)>", cell, row)

    return PdfTable(
        fixed_width(head[1], 0), [fixed_width(row, i + 1) for i, row in enumerate(rows)]
    )


def take_table_prefix(previous: str) -> tuple[str, str, str]:
    """Keep a labelled caption and an immediately preceding heading with the table."""
    caption = lead = ""
    paragraphs = list(re.finditer(r"<p(?:\s[^>]*)?>.*?</p>", previous, re.S))
    if paragraphs:
        last = paragraphs[-1]
        if not previous[last.end() :].strip() and re.match(
            r"\s*(?:表\s*[\d一二三四五六七八九十A-Z]|Table\s+[\dA-Z])", plain_html(last[0]), re.I
        ):
            caption, previous = last[0], previous[: last.start()]
    previous, lead = take_trailing_heading(previous)
    return previous, caption, lead


def take_trailing_heading(previous: str) -> tuple[str, str]:
    lead = ""
    headings = list(re.finditer(r"<h[1-6](?:\s[^>]*)?>.*?</h[1-6]>", previous, re.S))
    for last in reversed(headings):
        if previous[last.end() :].strip():
            break
        lead, previous = last[0] + lead, previous[: last.start()]
    return previous, lead
