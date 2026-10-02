"""Suggest exact continuous source spans; models select, code restores original bytes."""

from __future__ import annotations

import hashlib

from ..guardrails import _normalize_text, _normalize_with_offsets, _normalized_span
from ..models import ExtractionCandidate, FindingContent, QuoteOption, RepairFindingContent, Source


def quote_options(candidate: ExtractionCandidate, sources: list[Source]) -> list[QuoteOption]:
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
    start, end = span
    content_hash = hashlib.sha256(source.content.encode()).hexdigest()
    key = hashlib.sha256(f"{content_hash}:{start}:{end}".encode()).hexdigest()[:12]
    options = [
        QuoteOption(
            id=f"q-{candidate.id}-{key}",
            source_url=source.url,
            source_content_hash=content_hash,
            start=start,
            end=end,
            text=source.content[start:end],
        )
    ]
    if span != (0, len(source.content)):
        full_key = hashlib.sha256(f"{content_hash}:0:{len(source.content)}".encode()).hexdigest()[
            :12
        ]
        options.append(
            QuoteOption(
                id=f"q-{candidate.id}-full-{full_key}",
                source_url=source.url,
                source_content_hash=content_hash,
                start=0,
                end=len(source.content),
                text=source.content,
            )
        )
    return options


def resolve_quote(
    proposal: RepairFindingContent, candidate: ExtractionCandidate, sources: list[Source]
) -> tuple[FindingContent, list[str]]:
    content = FindingContent.model_validate(proposal.model_dump())
    if not proposal.quote_id:
        return content, []
    options = [option for option in candidate.quote_options if option.id == proposal.quote_id]
    if len(options) != 1:
        return content, ["repair_quote_id_not_allowed"]
    option = options[0]
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
