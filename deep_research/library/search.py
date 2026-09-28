"""Rank project-corpus chunks as a deterministic research search overlay."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

from ..models import Source
from ..tools.base import SearchTool
from .models import SearchChunk
from .repository import LibraryRepository

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.+-]*|[\u3400-\u9fff]+", re.IGNORECASE)
_MAX_QUERY_TERMS = 64
_MAX_RESULTS = 20
_BM25_K1 = 1.5
_BM25_B = 0.75


def _lexemes(value: str) -> Iterator[str]:
    for match in _TOKEN_RE.finditer(value.casefold()):
        token = match.group(0)
        if "\u3400" <= token[0] <= "\u9fff":
            if len(token) == 1:
                yield token
            else:
                yield from (token[index : index + 2] for index in range(len(token) - 1))
        else:
            yield token


def _terms(value: str) -> list[str]:
    return list(dict.fromkeys(_lexemes(value)))[:_MAX_QUERY_TERMS]


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _phrase_fragments(query: str) -> list[str]:
    fragments: list[str] = []
    normalized = _normalized(query)
    if 2 <= len(normalized) <= 160:
        fragments.append(normalized)
    latin = re.findall(r"[a-z0-9][a-z0-9_.+-]*", normalized)
    for size in (3, 2):
        fragments.extend(
            " ".join(latin[index : index + size]) for index in range(len(latin) - size + 1)
        )
    fragments.extend(
        run for run in re.findall(r"[\u3400-\u9fff]{2,24}", normalized) if run not in fragments
    )
    return list(dict.fromkeys(fragments))[:16]


def _field_stats(value: str, query_terms: set[str]) -> tuple[int, Counter[str]]:
    length = 0
    counts: Counter[str] = Counter()
    for token in _lexemes(value):
        length += 1
        if token in query_terms:
            counts[token] += 1
    return max(length, 1), counts


@dataclass(frozen=True)
class _Document:
    chunk: SearchChunk
    index: int
    content_length: int
    title_length: int
    section_length: int
    content_terms: Counter[str]
    title_terms: Counter[str]
    section_terms: Counter[str]
    phrase_score: float
    coverage: float

    @property
    def matched_terms(self) -> set[str]:
        return set(self.content_terms) | set(self.title_terms) | set(self.section_terms)


@dataclass(frozen=True)
class _RankedChunk:
    score: float
    index: int
    chunk: SearchChunk


def _document(
    chunk: SearchChunk,
    index: int,
    query_terms: set[str],
    fragments: list[str],
) -> _Document:
    content_length, content_terms = _field_stats(chunk.content, query_terms)
    title_length, title_terms = _field_stats(chunk.source_title, query_terms)
    section_length, section_terms = _field_stats(chunk.section, query_terms)
    normalized_content = _normalized(chunk.content)
    normalized_title = _normalized(chunk.source_title)
    normalized_section = _normalized(chunk.section)
    phrase_score = 0.0
    for position, fragment in enumerate(fragments):
        weight = 2.5 if position == 0 else 1.0
        if fragment in normalized_title:
            phrase_score += 3.0 * weight
        elif fragment in normalized_section:
            phrase_score += 2.0 * weight
        elif fragment in normalized_content:
            phrase_score += weight
    matched = set(content_terms) | set(title_terms) | set(section_terms)
    return _Document(
        chunk=chunk,
        index=index,
        content_length=content_length,
        title_length=title_length,
        section_length=section_length,
        content_terms=content_terms,
        title_terms=title_terms,
        section_terms=section_terms,
        phrase_score=phrase_score,
        coverage=len(matched) / max(len(query_terms), 1),
    )


def _bm25_term(tf: int, length: int, average_length: float) -> float:
    if tf <= 0:
        return 0.0
    denominator = tf + _BM25_K1 * (1.0 - _BM25_B + _BM25_B * length / max(average_length, 1.0))
    return tf * (_BM25_K1 + 1.0) / denominator


def _rank_chunks(query: str, chunks: list[SearchChunk]) -> list[_RankedChunk]:
    terms = _terms(query)
    if not terms or not chunks:
        return []
    query_terms = set(terms)
    fragments = _phrase_fragments(query)
    all_documents = [
        _document(chunk, index, query_terms, fragments) for index, chunk in enumerate(chunks)
    ]
    documents = [document for document in all_documents if document.matched_terms]
    if not documents:
        return []

    document_frequency: Counter[str] = Counter()
    for document in documents:
        document_frequency.update(document.matched_terms)
    count = len(all_documents)
    average_content = sum(document.content_length for document in all_documents) / count
    average_title = sum(document.title_length for document in all_documents) / count
    average_section = sum(document.section_length for document in all_documents) / count

    raw_scores: list[tuple[float, float, _Document]] = []
    for document in documents:
        bm25 = 0.0
        for term in terms:
            frequency = document_frequency[term]
            inverse_frequency = math.log(1.0 + (count - frequency + 0.5) / (frequency + 0.5))
            bm25 += inverse_frequency * (
                _bm25_term(document.content_terms[term], document.content_length, average_content)
                + 3.0 * _bm25_term(document.title_terms[term], document.title_length, average_title)
                + 1.8
                * _bm25_term(document.section_terms[term], document.section_length, average_section)
            )
        structural = (
            document.phrase_score
            + document.coverage * 2.0
            + len(document.title_terms) * 0.8
            + len(document.section_terms) * 0.4
        )
        raw_scores.append((bm25, structural, document))

    maximum_bm25 = max(item[0] for item in raw_scores) or 1.0
    maximum_structural = max(item[1] for item in raw_scores) or 1.0
    ranked = [
        _RankedChunk(
            score=0.78 * bm25 / maximum_bm25 + 0.22 * structural / maximum_structural,
            index=document.index,
            chunk=document.chunk,
        )
        for bm25, structural, document in raw_scores
    ]
    ranked.sort(key=lambda item: (-item.score, item.index))
    return ranked


def _select_diverse(ranked: list[_RankedChunk], limit: int) -> list[SearchChunk]:
    """Give distinct sources one result each before filling remaining slots."""

    selected: list[_RankedChunk] = []
    selected_ids: set[str] = set()
    seen_sources: set[str] = set()
    for item in ranked:
        if item.chunk.source_id in seen_sources:
            continue
        selected.append(item)
        selected_ids.add(item.chunk.id)
        seen_sources.add(item.chunk.source_id)
        if len(selected) >= limit:
            return [entry.chunk for entry in selected]
    for item in ranked:
        if item.chunk.id in selected_ids:
            continue
        selected.append(item)
        if len(selected) >= limit:
            break
    return [entry.chunk for entry in selected]


def _chunk_url(origin_url: str, source_id: str, ordinal: int) -> str:
    if not origin_url:
        return f"https://workspace.invalid/sources/{source_id}?chunk={ordinal + 1}"
    parsed = urlsplit(origin_url)
    suffix = f"dr_source={quote(source_id)}&dr_chunk={ordinal + 1}"
    query = f"{parsed.query}&{suffix}" if parsed.query else suffix
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


class ProjectCorpusSearch(SearchTool):
    def __init__(self, repository: LibraryRepository, project_id: str, owner_id: str) -> None:
        self.repository = repository
        self.project_id = project_id
        self.owner_id = owner_id

    @property
    def backend_name(self) -> str:
        return "ProjectCorpus"

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        requested = min(max_results, _MAX_RESULTS)
        if requested <= 0 or not query.strip():
            return []
        chunks = await self.repository.search_chunks(
            self.project_id, owner_id=self.owner_id, limit=5000
        )
        selected = _select_diverse(_rank_chunks(query, chunks), requested)
        return [
            Source(
                title=chunk.source_title,
                url=_chunk_url(chunk.origin_url, chunk.source_id, chunk.ordinal),
                content=chunk.content,
                locator=chunk.locator,
            )
            for chunk in selected
        ]
