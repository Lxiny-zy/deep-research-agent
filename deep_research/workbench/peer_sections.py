"""Bound peer-review method units to exact ranges of immutable source snapshots."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from ..guardrails import _normalize_text, _normalized_span
from ..models import Finding, ResearchResult, Source
from ..tools.oa_pdf_fulltext import _NUMBERING_RE, _heading_kind, _line_heading_kind
from .delivery.math_markdown import citation_text
from .review_coverage import _findings
from .support import digest


def _number(title: str) -> tuple[str, ...] | None:
    match = _NUMBERING_RE.match(title.strip())
    return tuple(re.findall(r"\d+|[IVXLC]+", match[0].upper())) if match else None


def _method(title: str, kind: str | None) -> bool:
    if kind == "method":
        return True
    if kind in {"abstract", "introduction", "results", "experiment", "conclusion", "references"}:
        return False
    return bool(re.search(
        r"\b(?:method(?:ology)?s?|formulation|algorithm|architecture|framework|proof)\b"
        r"|方法|理论框架|算法|模型架构|形式化|证明", title, re.I,
    )) and not re.search(r"\b(?:related|prior|existing)\b|相关工作|已有方法", title, re.I)


@dataclass(frozen=True)
class SectionPart:
    source: Source
    title: str
    kind: str | None
    start: int
    body_start: int
    end: int
    level: int | None = None

    def binding(self) -> tuple:
        return (self.source.url, hashlib.sha256(self.source.content.encode()).hexdigest(),
                self.title, self.kind, self.start, self.body_start, self.end)


@dataclass
class MethodUnit:
    id: str
    title: str
    parts: list[SectionPart] = field(default_factory=list)

    def sources(self) -> list[Source]:
        unique = {(part.source.url, part.binding()[1]): part.source for part in self.parts}
        return list(unique.values())

    def findings(self, results: list[ResearchResult]) -> list[Finding]:
        selected = []
        for finding in _findings(results, self.sources()):
            for part in self.parts:
                source = part.source
                if (finding.source_url != source.url
                        or finding.verification.source_content_hash != part.binding()[1]):
                    continue
                start, end = finding.verification.quote_start, finding.verification.quote_end
                if start is None or end is None:
                    quote = _normalize_text(finding.evidence_quote)
                    if not quote or _normalize_text(source.content).count(quote) != 1:
                        continue
                    span = _normalized_span(source.content, finding.evidence_quote)
                    if span is None:
                        continue
                    start, end = span
                if (part.start <= start < end <= part.end and end > part.body_start
                        and _normalize_text(source.content[start:end])
                        == _normalize_text(finding.evidence_quote)):
                    selected.append(finding)
                    break
        return selected

    def context(self, sources: list[Source]) -> str:
        from ..agents.researcher import source_context

        scoped = []
        for source in sources:
            snapshot = hashlib.sha256(source.content.encode()).hexdigest()
            spans = [
                p for p in self.parts if p.source.url == source.url and p.binding()[1] == snapshot
            ]
            text = "\n\n".join(source.content[p.start:p.end] for p in spans)
            scoped.append(source.model_copy(update={"content": text}))
        # Only the prompt is narrowed. Verification still uses the original snapshots.
        return source_context(scoped)


def _parts(source: Source) -> list[SectionPart]:
    text = source.content
    masked = citation_text(text)
    headings: list[tuple[int, int, str, str | None, int | None]] = []
    section_source = source.scholarly is not None and re.search(
        r"[?&]dr_section=(?:pdf-)?\d+(?:&|$)", source.url,
    ) is not None
    for match in re.finditer(r"(?m)^([^\n]*)(?:\n|$)", masked):
        line = text[match.start():match.start() + len(match[1])].strip()
        markdown = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        title = markdown[2] if markdown else line
        kind = _line_heading_kind(title)
        first = not text[:match.start()].strip()
        if title and (markdown or _NUMBERING_RE.match(title) and kind is not None
                      or first and (kind is not None or section_source)):
            headings.append((match.start(), match.end(), title, _heading_kind(title),
                             len(markdown[1]) if markdown else None))
    label = source.section_title or source.locator or "正文（未识别章节）"
    base_kind = _heading_kind(label) or (source.scholarly.section if source.scholarly else None)
    if not headings or text[:headings[0][0]].strip():
        headings.insert(0, (0, 0, label, base_kind, None))
    parts = []
    for i, (start, body, title, kind, level) in enumerate(headings):
        end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        parts.append(SectionPart(source, title, kind, start, body, end, level))
    return parts


def method_units(document: str, sources: list[Source]) -> list[MethodUnit]:
    parts = [part for source in sources for part in _parts(source)]
    roots = []
    for part in parts:
        if _method(part.title, part.kind) and (number := _number(part.title)):
            roots.append(number if len(number) == 1 else number[:-1])
    chosen = []
    active = False
    active_level: int | None = None
    for part in parts:
        number = _number(part.title)
        if number:
            active = any(number[:len(root)] == root for root in roots)
        elif _method(part.title, part.kind):
            active, active_level = True, part.level
        elif part.kind in {"abstract", "introduction", "results", "experiment", "conclusion",
                          "references", "other"} or (
            part.level is not None and active_level is not None and part.level <= active_level
        ):
            active = False
        if active and part.source.content[part.body_start:part.end].strip():
            chosen.append(part)
    if not chosen:
        # Nonstandard or unstructured papers still need their substantive argument checked.
        # Abstract-only inputs cannot become a complete peer review.
        chosen = [p for p in parts if p.kind not in {"abstract", "introduction", "references"}
                  and p.source.content[p.body_start:p.end].strip()]
    units: dict[str, MethodUnit] = {}
    for part in chosen:
        key = digest([document, " ".join(part.title.casefold().split())])
        units.setdefault(key, MethodUnit(key, part.title)).parts.append(part)
    return list(units.values())
