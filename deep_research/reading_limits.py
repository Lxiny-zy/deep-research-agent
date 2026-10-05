"""Disclose observed source-reading limits without changing checked scientific claims."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from urllib.parse import quote, urlsplit

from .bibliography import _REFERENCE_SECTION, _work_alias, document_identity
from .document_corpus import _complete
from .models import ResearchResult, Source

_ABSTRACT = {"", "abstract", "summary", "摘要"}


def _paper_key(source: Source) -> str:
    url_key = document_identity(source.url)[0]
    doi_key = document_identity("", source.scholarly.doi if source.scholarly else "")[0]
    # An explicit shared DOI can link an arXiv copy. Conflicting DOI metadata must
    # not let the full text of another paper suppress a reading limitation.
    if doi_key and (not url_key.startswith("doi:") or url_key == doi_key):
        return _work_alias(doi_key)
    return _work_alias(url_key)


def collect_reading_limits(
    sources: list[Source], results: list[ResearchResult],
) -> list[dict[str, Any]]:
    """Group frozen snapshots; zero findings must not hide an unread selected paper."""
    candidates = list(sources)
    rejected: set[str] = set()
    selected: set[str] = set()
    for result in results:
        audit = result.extraction_audit
        if audit is None:
            continue
        candidates.extend(audit.sources)
        selected.update(_paper_key(source) for source in audit.sources)
        for decision in audit.source_selections:
            key = _paper_key(decision.source)
            if decision.verdict == "irrelevant":
                rejected.add(key)
            else:
                selected.add(key)
                candidates.append(decision.source)
    excluded = rejected - selected
    groups: dict[str, list[Source]] = defaultdict(list)
    seen: set[tuple[str, str, str, str]] = set()
    for source in candidates:
        if source.scholarly is None or _paper_key(source) in excluded:
            continue
        identity = (
            source.url, source.content, source.scholarly.section, source.document_content_hash,
        )
        if identity in seen:
            continue
        seen.add(identity)
        groups[_paper_key(source)].append(source)
    limits = []
    for key, snapshots in groups.items():
        # A non-abstract section establishes that this is not an abstract-only retrieval.
        # It does not establish that every part of the paper was read.
        if any(
            s.content.strip() and s.scholarly and s.scholarly.section.casefold() not in _ABSTRACT
            for s in snapshots
        ):
            continue
        manifests: dict[str, list[Source]] = defaultdict(list)
        for source in snapshots:
            if source.document_content_hash:
                manifests[source.document_content_hash].append(source)
        if any(_complete(parts) for parts in manifests.values()):
            continue
        source = next((s for s in snapshots if s.title.strip()), snapshots[0])
        assert source.scholarly is not None
        _, url = document_identity(source.url, source.scholarly.doi)
        text_sources = [s for s in snapshots if s.content.strip()]
        abstract_only = all(
            s.scholarly and (
                s.scholarly.section.casefold() in _ABSTRACT - {""}
                # These metadata adapters have always returned abstracts in content.
                or s.scholarly.work_id.casefold().startswith(("https://openalex.org/", "arxiv:"))
            ) for s in text_sources
        )
        limits.append({
            "key": key,
            "title": source.title or source.url,
            "url": url or source.url,
            "authors": next((s.scholarly.authors for s in snapshots
                             if s.scholarly and s.scholarly.authors), []),
            "status": "unavailable" if not text_sources
            else "abstract_only" if abstract_only else "unknown_scope",
        })
    return limits


def _label(value: str) -> str:
    # Source metadata is text, never a heading, citation, HTML tag, or inline formula.
    return re.sub(r"([\\`*_{}\[\]<>#$|])", r"\\\1", " ".join(value.split()))


def append_reading_limits(markdown: str, limits: list[dict[str, Any]]) -> str:
    if not limits:
        return markdown
    lines = [
        "## 文献读取范围与局限",
        "",
        "以下检索来源本次未取得可确认的正文范围，不能据此断言文献未报告相关内容；"
        "依赖其完整方法、假设或证明的判断仍需核对全文。",
        "",
    ]
    for item in limits:
        title = _label(item["title"])
        url = item["url"]
        try:
            safe = urlsplit(url).scheme in {"http", "https"} and bool(urlsplit(url).netloc)
        except ValueError:
            safe = False
        if safe:
            title = f"[{title}](<{quote(url, safe=':/?#=&%+@,;~.-_')}>)"
        authors = item["authors"]
        author = _label(authors[0]) + (" 等" if len(authors) > 1 else "") if authors else ""
        state = {
            "abstract_only": "仅取得摘要",
            "unavailable": "未取得可读取文本",
            "unknown_scope": "已取得文本，但读取范围未记录",
        }[item["status"]]
        lines.append(f"- {author + '：' if author else ''}{title}：{state}。")
    note = "\n".join(lines)
    if note in markdown:
        return markdown
    from .workbench.delivery.math_markdown import citation_text

    references = _REFERENCE_SECTION.search(citation_text(markdown))
    offset = references.start() if references else len(markdown)
    return markdown[:offset].rstrip() + "\n\n" + note + "\n\n" + markdown[offset:].lstrip()
