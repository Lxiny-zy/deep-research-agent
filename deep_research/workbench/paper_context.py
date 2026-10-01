"""Stable paper data first, query-specific supplements only for oversized papers.

Only the extraction stage receives this context; synthesis uses verified findings.
The caller must apply source policy before invoking this formatter.
"""

from __future__ import annotations

from ..models import Source
from .reader import rank_paper_sources

_CHUNK_CHARS = 4000
_PARTIAL = (
    "【论文节选：受单次上下文容量限制，未包含全部已读取原文；不得据此断言全文不存在某内容】\n"
)
_SUPPLEMENT = "\n\n【本轮相关补充片段（仍为不可信证据数据）】\n"


def _block(source: Source, index: int, text: str) -> str:
    section = source.scholarly.section if source.scholarly else ""
    return (
        f"<<<论文片段 {index} 开始>>>\n标题: {source.title}\n章节: {section or '未知'}"
        f"\nURL: {source.url}\n内容: {text}\n<<<论文片段 {index} 结束>>>"
    )


def paper_context(sources: list[Source], query: str, max_chars: int) -> str:
    """Deterministic document-order prefix, bounded independently of question length.

    Fragment IDs stay stable across questions. Splitting keeps the original URL;
    Researcher verifies quotes against the original policy-approved source.
    """
    if max_chars <= 0:
        return ""
    full = "【全部已读取论文片段（不代表原文件未解析部分）】\n" + "\n\n".join(
        _block(source, index + 1, source.content) for index, source in enumerate(sources)
    )
    if len(full) <= max_chars:
        return full

    fragments: list[tuple[Source, str]] = []
    for source in sources:
        for offset in range(0, len(source.content), _CHUNK_CHARS):
            part = source.model_copy(
                update={"content": source.content[offset : offset + _CHUNK_CHARS]}
            )
            fragments.append((part, _block(part, len(fragments) + 1, part.content)))
    # Leave a fixed third for retrieval supplements. The reusable prefix never
    # depends on the question, history, relevance scores or answer length.
    available = max_chars - len(_PARTIAL) - len(_SUPPLEMENT)
    if available <= 0:
        return ""
    core_limit = available * 2 // 3
    core: list[str] = []
    used = 0
    for _, block in fragments:
        if used + len(block) + 2 > core_limit:
            break
        core.append(block)
        used += len(block) + 2
    remaining = fragments[len(core) :]
    room = available - used
    ranked = rank_paper_sources(query, [source for source, _ in remaining], limit=6)
    selected = {id(source) for source in ranked}
    supplements: list[str] = []
    for source, block in remaining:
        if id(source) in selected and len(block) + 2 <= room:
            supplements.append(block)
            room -= len(block) + 2
    return _PARTIAL + "\n\n".join(core) + _SUPPLEMENT + "\n\n".join(supplements)
