"""Bind all parsed document parts before permitting claims about textual absence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .bibliography import document_identity
from .models import ResearchResult, Source


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def manifest_hash(sources: list[Source]) -> str:
    payload = [(source.url, content_hash(source.content)) for source in sources]
    return content_hash(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def mark_complete_sources(sources: list[Source]) -> list[Source]:
    """Called only with every part of one completely parsed input document."""
    manifest = manifest_hash(sources)
    return [
        source.model_copy(
            update={
                "document_content_hash": manifest,
                "document_part_index": index,
                "document_part_count": len(sources),
            }
        )
        for index, source in enumerate(sources)
    ]


def work_key(source: Source) -> str:
    return document_identity(source.url, source.scholarly.doi if source.scholarly else "")[0]


@dataclass(frozen=True)
class CorpusDocument:
    key: str
    title: str
    sources: tuple[Source, ...]
    complete: bool


def _complete(sources: list[Source]) -> bool:
    if not sources or any(not source.content.strip() for source in sources):
        return False
    hashes = {source.document_content_hash for source in sources}
    counts = {source.document_part_count for source in sources}
    if len(hashes) != 1 or "" in hashes or counts != {len(sources)}:
        return False
    if {source.document_part_index for source in sources} != set(range(len(sources))):
        return False
    ordered = sorted(sources, key=lambda source: source.document_part_index or 0)
    return hashes == {manifest_hash(ordered)}


class FullTextCorpus:
    def __init__(self, sources: list[Source], mapping: dict[str, int]) -> None:
        # Identical snapshots can occur in more than one subquestion audit.
        unique: dict[str, Source] = {}
        for source in sources:
            key = source.model_dump_json(
                exclude={
                    "content_hash",
                    "document_content_hash",
                    "document_part_index",
                    "document_part_count",
                }
            )
            previous = unique.get(key)
            if previous and previous.document_content_hash and source.document_content_hash:
                if previous.document_content_hash != source.document_content_hash:
                    key += source.document_content_hash
            if (
                previous is None
                or source.document_content_hash
                or not previous.document_content_hash
            ):
                unique[key] = source.model_copy(deep=True)
        selected_works = {work_key(source) for source in unique.values() if source.url in mapping}
        groups: dict[str, list[Source]] = {}
        for source in unique.values():
            key = work_key(source)
            if not mapping or key in selected_works:
                groups.setdefault(key, []).append(source)
        for parts in groups.values():
            parts.sort(
                key=lambda source: (
                    source.document_part_index if source.document_part_index is not None else -1,
                    source.url,
                    content_hash(source.content),
                )
            )
        self.documents = {
            key: CorpusDocument(key, parts[0].title or key, tuple(parts), _complete(parts))
            for key, parts in groups.items()
        }
        self.citations = {
            index: work_key(source)
            for source in unique.values()
            if (index := mapping.get(source.url)) is not None
        }
        self.fingerprint = content_hash(
            json.dumps(
                {
                    "documents": {
                        key: {
                            "complete": doc.complete,
                            "parts": [
                                (part.url, content_hash(part.content), part.locator)
                                for part in doc.sources
                            ],
                        }
                        for key, doc in sorted(self.documents.items())
                    },
                    "citations": self.citations,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
        )

    def catalog(self, citations: list[int]) -> list[dict[str, Any]]:
        if citations and any(index not in self.citations for index in citations):
            return []
        selected = {self.citations[index] for index in citations if index in self.citations}
        return [
            {
                "id": doc.key,
                "title": doc.title,
                "complete": doc.complete,
                "sources": [
                    {"url": source.url, "locator": source.locator} for source in doc.sources
                ],
            }
            for doc in self.documents.values()
            if not selected or doc.key in selected
        ]


def corpus_from_inputs(
    results: list[ResearchResult],
    mapping: dict[str, int],
    scratch: dict[str, Any] | None = None,
    sources: list[Source] | None = None,
) -> FullTextCorpus:
    collected = [
        source
        for result in results
        if result.extraction_audit
        for source in result.extraction_audit.sources
    ]
    collected.extend(sources or [])
    if scratch:
        from .workbench.attachments import attachments_from_scratch

        for attachment in attachments_from_scratch(scratch):
            collected.extend(attachment.sources())
        for item in scratch.get("paper_sources", []):
            collected.append(Source.model_validate(item))
    return FullTextCorpus(collected, mapping)
