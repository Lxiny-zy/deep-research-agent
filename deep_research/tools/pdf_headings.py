"""Lightweight layout evidence for distinguishing PDF headings from table cells."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PdfLine:
    text: str
    size: float
    bold: bool
    baseline: float
    box: tuple[float, float, float, float]
    column_gaps: bool = False


def line_layout(text: str, spans: list[dict[str, Any]]) -> PdfLine:
    boxes = [s["bbox"] for s in spans if s.get("bbox")]
    box = (
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    ) if boxes else (0.0, 0.0, 0.0, 0.0)
    size = max((float(s.get("size", 0)) for s in spans), default=0)
    weight = sum(len(s.get("text", "").strip()) for s in spans)
    bold = sum(
        len(s.get("text", "").strip()) for s in spans
        if s.get("flags", 0) & 16 or re.search(r"bold|medi|demi|black", s.get("font", ""), re.I)
    )
    gaps = any(
        left.get("bbox") and right.get("bbox")
        and right["bbox"][0] - left["bbox"][2] > max(20, size * 2.5)
        for left, right in zip(spans, spans[1:], strict=False)
    )
    baseline = float(spans[0].get("origin", (0, box[3]))[1]) if spans else box[3]
    return PdfLine(text, size, bool(weight and bold / weight >= 0.75), baseline, box, gaps)


def body_font_size(pages: list[list[PdfLine]]) -> float:
    sizes: Counter[float] = Counter()
    for page in pages:
        for line in page:
            if line.size > 0:
                sizes[round(line.size, 1)] += len(line.text.strip())
    remaining = sum(sizes.values()) / 2
    for size, weight in sorted(sizes.items()):
        remaining -= weight
        if remaining <= 0:
            return size
    return 0.0


def _cell(line: PdfLine) -> bool:
    text = line.text.strip()
    return bool(text) and len(text) <= 60 and not text.endswith(("。", ";", ":", "."))


def _same_column(a: PdfLine, b: PdfLine) -> bool:
    return (
        min(abs(a.box[0] - b.box[0]), abs(a.box[2] - b.box[2])) <= max(4, a.size * 1.5)
        or min(a.box[2], b.box[2]) > max(a.box[0], b.box[0])
    )


def _table_label(line: PdfLine, page: list[PdfLine]) -> bool:
    if line.column_gaps:
        return True
    if not _cell(line):
        return False
    peers = [
        other for other in page if other is not line and _cell(other)
        and abs(other.baseline - line.baseline) <= max(0.75, line.size * 0.1)
        and (other.box[0] > line.box[2] or line.box[0] > other.box[2])
    ]
    if not peers:
        return False
    below = [
        other for other in page if _cell(other)
        and 0 < other.baseline - line.baseline <= max(20, line.size * 5)
    ]
    rows = set()
    for left in below:
        if not _same_column(line, left):
            continue
        for right in below:
            if right is left or abs(right.baseline - left.baseline) > 1:
                continue
            if any(_same_column(peer, right) for peer in peers) and re.search(
                r"\d", left.text + right.text,
            ):
                rows.add(round(left.baseline))
    return len(rows) >= 2


def heading_allowed(line: PdfLine, page: list[PdfLine], body_size: float, *, custom: bool) -> bool:
    if line.size and body_size and line.size < body_size * 0.85:
        return False
    if _table_label(line, page):
        return False
    # Unknown numbered titles need typographic support; ordinary numbered prose
    # and mathematical expressions must not create new source boundaries.
    return not custom or line.bold or (body_size > 0 and line.size > body_size + 0.7)
