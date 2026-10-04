"""Suggest exact continuous source spans; models select, code restores original bytes."""

from __future__ import annotations

import hashlib
import re

from ..guardrails import _normalize_text, _normalize_with_offsets, _normalized_span
from ..models import ExtractionCandidate, FindingContent, QuoteOption, RepairFindingContent, Source


def _short_spans(
    text: str, start: int, end: int, statement: str, limit: int
) -> list[tuple[int, int]]:
    """Suggest short literal windows; only semantic verification can admit them."""
    boundaries = [start, *(
        start + match.end()
        for match in re.finditer(r"[。！？；\n]|(?<!\d)[.!?;](?=\s|$)", text[start:end])
    ), end]
    spans: set[tuple[int, int]] = set()
    tokens = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", statement.casefold()))
    for i, left in enumerate(boundaries[:-1]):
        while left < end and text[left].isspace():
            left += 1
        right = boundaries[i + 1]
        while right > left and text[right - 1].isspace():
            right -= 1
        if right - left <= limit:
            if right - left >= 6:
                spans.add((left, right))
            # Keep adjacent attribution/conditions available without ever
            # concatenating disjoint passages or supplying the full source.
            if i + 2 < len(boundaries):
                adjacent = boundaries[i + 2]
                while adjacent > right and text[adjacent - 1].isspace():
                    adjacent -= 1
                if 6 <= adjacent - left <= limit:
                    spans.add((left, adjacent))
        else:
            # A PDF may have no sentence boundaries. These are suggestions,
            # not automatic truncations of an already approved quotation.
            for match in re.finditer(r"[A-Za-z0-9]+|[\u3400-\u9fff]", text[left:right]):
                if match[0].casefold() not in tokens:
                    continue
                a = max(left, min(left + match.start() - limit // 3, right - limit))
                spans.add((a, a + limit))
    ranked = sorted(spans, key=lambda span: (
        -len(tokens & set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", text[slice(*span)].casefold()))),
        span[1] - span[0], span[0],
    ))
    # Full frozen sources remain in the repair prompt; these are only convenient
    # choices. The model may select another exact, bounded continuous quotation.
    selected: list[tuple[int, int]] = []
    seen: set[str] = set()
    for span in ranked:
        value = text[slice(*span)]
        if value not in seen:
            selected.append(span)
            seen.add(value)
        if len(selected) == 12:
            break
    return selected


def quote_options(
    candidate: ExtractionCandidate, sources: list[Source], *, max_quote_chars: int = 600
) -> list[QuoteOption]:
    own = {
        source.model_dump_json(): source
        for source in sources
        if source.url == candidate.original.source_url
    }
    if len(own) != 1:
        return []
    source = next(iter(own.values()))
    quote = candidate.original.evidence_quote.strip()
    if not quote:
        return []
    span = _normalized_span(source.content, quote)
    if span is None:
        text, offsets = _normalize_with_offsets(source.content)
        needle = _normalize_text(quote)
        # These are search anchors, not a relaxed admission rule. The entire
        # intervening source (including headers/control glyphs) is retained.
        # Nonunique anchors deliberately produce no suggestion.
        width = min(48, len(needle) // 3)
        if width < 16:
            return []
        left, right = needle[:width], needle[-width:]
        if text.count(left) != 1 or text.count(right) != 1:
            return []
        start, end = text.index(left), text.index(right) + width
        if end - width < start + width:
            return []
        span = offsets[start], offsets[end - 1] + 1
    content_hash = hashlib.sha256(source.content.encode()).hexdigest()
    spans = [span] if span[1] - span[0] <= max_quote_chars else _short_spans(
        source.content, *span, candidate.original.statement, max_quote_chars
    )
    options = []
    for start, end in spans:
        key = hashlib.sha256(f"{content_hash}:{start}:{end}".encode()).hexdigest()[:12]
        options.append(
            QuoteOption(
                id=f"q-{candidate.id}-{key}",
                source_url=source.url,
                source_content_hash=content_hash,
                start=start,
                end=end,
                text=source.content[start:end],
            )
        )
    return options


def resolve_quote(
    proposal: RepairFindingContent, candidate: ExtractionCandidate, sources: list[Source],
    *, max_quote_chars: int = 600,
) -> tuple[FindingContent, list[str]]:
    content = FindingContent.model_validate(proposal.model_dump())
    if not proposal.quote_id:
        return content, []
    options = [option for option in candidate.quote_options if option.id == proposal.quote_id]
    if len(options) != 1:
        return content, ["repair_quote_id_not_allowed"]
    option = options[0]
    if len(option.text.strip()) > max_quote_chars:
        return content, ["evidence_quote_too_long"]
    valid = any(
        source.url == option.source_url == proposal.source_url == candidate.original.source_url
        and hashlib.sha256(source.content.encode()).hexdigest() == option.source_content_hash
        and 0 <= option.start < option.end <= len(source.content)
        and source.content[option.start : option.end] == option.text
        for source in sources
    )
    if not valid:
        return content, ["repair_quote_snapshot_mismatch"]
    if content.evidence_quote and content.evidence_quote != option.text:
        return content, ["repair_quote_reference_conflict"]
    return content.model_copy(update={"evidence_quote": option.text}), []
