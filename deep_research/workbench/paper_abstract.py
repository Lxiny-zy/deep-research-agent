"""Bind complete abstract spans to frozen paper bytes, solely for translation."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from ..guardrails import SourcePolicy, screen_source_intent
from ..models import Source
from .reader import paper_sources_from_scratch

ABSTRACT_KEY = "paper_abstracts"
TRANSLATION_TITLES = (
    "摘要翻译",
    "摘要中文翻译",
    "摘要的忠实中文翻译",
    "摘要译文",
    "abstract translation",
)
_START = re.compile(
    r"(?:^|\n)[^\S\r\n]*(?:#{1,6}[^\S\r\n]*)?(?:abstract|摘要|summary)"
    r"[^\S\r\n]*(?:[—–:：-][^\S\r\n]*|\r*\n)",
    re.I,
)
_END = re.compile(
    r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:index\s+terms|keywords|key\s+words|关键词)\b"
    r"|(?:^|\n)\s*(?:#{1,6}\s*|(?:I|1)\.?\s+)?(?:introduction|引言)[^\S\r\n]*(?=\r*\n|$)",
    re.I,
)


def translation_title(title: str) -> bool:
    return any(key in title.casefold() for key in TRANSLATION_TITLES)


def abstract_span(source: Source) -> tuple[int, int] | None:
    text = source.content
    start = _START.search(text)
    declared = bool(
        source.scholarly
        and source.scholarly.section.strip().casefold() in {"abstract", "摘要", "summary"}
    ) or bool(
        getattr(source, "section_start", False)
        and getattr(source, "section_end", False)
        and getattr(source, "section_title", "").strip().casefold()
        in {"abstract", "摘要", "summary"}
    )
    if start is None and not declared:
        return None
    begin = start.end() if start else 0
    end = _END.search(text, begin)
    # Unstructured chunks can end halfway through an abstract. Never silently
    # call that the complete source just because a byte/chunk boundary was met.
    if end is None and not declared:
        return None
    finish = end.start() if end else len(text)
    while begin < finish and text[begin].isspace():
        begin += 1
    while finish > begin and text[finish - 1].isspace():
        finish -= 1
    return (begin, finish) if finish > begin else None


def _chunk_key(source: Source) -> tuple[str, int, bool] | None:
    url = urlsplit(source.url)
    chunk = parse_qs(url.query).get("chunk", [""])[0]
    if (
        url.hostname == "workspace.invalid"
        and url.path.startswith(("/attachments/", "/pasted/"))
        and chunk.isdigit()
    ):
        query = urlencode([(key, value) for key, value in parse_qsl(url.query) if key != "chunk"])
        return (
            urlunsplit((url.scheme, url.netloc, url.path, query, "")),
            int(chunk),
            url.path.startswith("/pasted/"),
        )
    fragment = re.fullmatch(r"chunk-(\d+)", url.fragment.rsplit("#", 1)[-1])
    if fragment:
        return (
            urlunsplit((url.scheme, url.netloc, url.path, url.query, "")),
            int(fragment[1]),
            False,
        )
    return None


def _candidates(sources: list[Source]) -> list[tuple[Source, list[Source]]]:
    """Restore overlapping, consecutive chunks only within the same uploaded file."""
    from ..library.ingestion import CHUNK_OVERLAP

    numbered: dict[tuple[str, int, bool], Source] = {}
    ambiguous: set[tuple[str, bool]] = set()
    for source in sources:
        key = _chunk_key(source)
        if key is not None:
            previous = numbered.get(key)
            if previous is not None and (
                previous.content != source.content or previous.title != source.title
            ):
                ambiguous.add((key[0], key[2]))
            numbered.setdefault(key, source)
    candidates = []
    for source in sources:
        if abstract_span(source) is not None:
            candidates.append((source, [source]))
            continue
        if not _START.search(source.content):
            continue
        key = _chunk_key(source)
        if key is None:
            continue
        family, current, pasted = key
        if (family, pasted) in ambiguous:
            continue
        text, members = source.content, [source]
        while (following := numbered.get((family, current + 1, pasted))) is not None:
            overlap = next(
                (
                    n
                    for n in range(min(CHUNK_OVERLAP, len(text), len(following.content)), 63, -1)
                    if text.endswith(following.content[:n])
                ),
                0,
            )
            # Without matching overlap we cannot prove continuity from these
            # snapshots; do not silently concatenate unrelated section content.
            if (not pasted and not overlap) or following.title != source.title:
                break
            text += "\n" + following.content if pasted else following.content[overlap:]
            members.append(following)
            whole_section = (
                getattr(source, "section_start", False)
                and getattr(following, "section_end", False)
                and all(
                    getattr(part, "section_title", "") == getattr(source, "section_title", "")
                    for part in members
                )
            )
            joined = source.model_copy(update={"content": text, "section_end": whole_section})
            if abstract_span(joined) is not None:
                candidates.append((joined, members))
                break
            current += 1
    return candidates


def _parts(sources: list[Source]) -> list[dict[str, str]]:
    return [
        {
            "source_url": source.url,
            "source_hash": hashlib.sha256(source.content.encode()).hexdigest(),
        }
        for source in sources
    ]


async def prepare_abstracts(
    scratch: dict[str, Any], *, screen_intent: bool
) -> list[dict[str, Any]]:
    records = []
    seen = set()
    policy = SourcePolicy()
    for source, members in _candidates(paper_sources_from_scratch(scratch)):
        span = abstract_span(source)
        if span is None:
            continue
        allowed = True
        for part in [source, *members[1:]]:
            decision = policy.evaluate(part)
            if screen_intent:
                decision = await screen_source_intent(part, decision)
            if not decision.allowed:
                allowed = False
                break
        if not allowed:
            continue
        start, end = span
        text = source.content[start:end]
        key = re.sub(r"\s+", " ", text).strip()
        if key in seen:
            continue
        seen.add(key)
        records.append(
            {
                "source_url": source.url,
                "source_title": source.title,
                "source_hash": hashlib.sha256(source.content.encode()).hexdigest(),
                "start": start,
                "end": end,
                "text": text,
                **({"parts": _parts(members)} if len(members) > 1 else {}),
            }
        )
    scratch[ABSTRACT_KEY] = {"version": 1, "items": records}
    return records


def checked_abstracts(scratch: dict[str, Any]) -> list[dict[str, Any]]:
    raw = scratch.get(ABSTRACT_KEY)
    if (
        not isinstance(raw, dict)
        or raw.get("version") != 1
        or not isinstance(raw.get("items"), list)
    ):
        return []
    sources = {
        (s.url, hashlib.sha256(s.content.encode()).hexdigest()): (s, members)
        for s, members in _candidates(paper_sources_from_scratch(scratch))
    }
    records = []
    for record in raw["items"]:
        if not isinstance(record, dict):
            return []
        url, source_hash = record.get("source_url"), record.get("source_hash")
        if not isinstance(url, str) or not isinstance(source_hash, str):
            return []
        found = sources.get((url, source_hash))
        if found is None:
            return []
        source, members = found
        if not all(SourcePolicy().evaluate(s).allowed for s in [source, *members[1:]]):
            return []
        if len(members) > 1 and record.get("parts") != _parts(members):
            return []
        span = abstract_span(source)
        if span is None or span != (record.get("start"), record.get("end")):
            return []
        if source.content[span[0] : span[1]] != record.get("text"):
            return []
        if source.title != record.get("source_title"):
            return []
        records.append(dict(record))
    return records


def abstract_section_support(scratch: dict[str, Any]) -> dict[str, str]:
    from .contract import contract_from_scratch

    workbench = scratch.get("workbench", {})
    template = workbench.get("template") if isinstance(workbench, dict) else None
    contract = contract_from_scratch(scratch)
    if template != "paperRead" and not (contract and contract.template == "paperRead"):
        return {}
    text = "\n\n".join(record["text"] for record in checked_abstracts(scratch))
    return dict.fromkeys(TRANSLATION_TITLES, text)
