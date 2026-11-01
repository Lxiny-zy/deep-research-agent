"""有序、有界的全文展开：把「逐条目串行抓全文」换成小窗口并发。

检索后端先拿到一页元数据条目，再逐条下载全文并切成章节来源。逐条 await 让
一次检索的延迟等于所有下载之和。这里按固定大小的窗口并发展开：

- **保序**：窗口内并发，结果仍按条目原顺序拼接（相关性排序是检索后端给的）；
- **文献数量**：``limit`` 限定待展开的文献，所选文献的章节完整保留；
- **有界**：窗口大小即对上游的最大并发，避免对 arXiv / OA 源站突发请求。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from ..models import Source
from .base import required_sections

T = TypeVar("T")
R = TypeVar("R")


# 开放检索每个检索式最多读几篇全文、每篇读多少。一个子问题有多个检索式、多个学术
# 后端，不设限时单次任务要读上百篇；其余相关文献保留摘要，仍可作为证据。
SEARCH_FULLTEXT_PAPERS = 3
SEARCH_FULLTEXT_CHARS = 20_000


def cap_fulltext(sections: list[Source], query: str, max_chars: int) -> list[Source]:
    """开放检索展开的一篇论文按总字数截取，放不下的整节跳过，不截断章节。

    选章器整节保留必需章节，问题点名方法、实验或结果时几乎整篇都是必需的（线上一篇 4–5 万字，
    一个子问题读入 30 万字，抽取输出反复截断）。这里摘要优先，其次是问题点名的章节类型，
    同类中短的先放，一节长推导不会挤掉结果表与结论；精读与评审按编号取论文，不经过这里。
    """
    if sum(len(source.content) for source in sections) <= max_chars:
        return sections
    wanted = required_sections(query)

    def rank(item: tuple[int, Source]) -> tuple[bool, bool, int, int]:
        index, source = item
        kind = source.scholarly.section if source.scholarly else ""
        return kind != "abstract", kind not in wanted, len(source.content), index

    chosen: set[int] = set()
    used = 0
    for index, source in sorted(enumerate(sections), key=rank):
        if not chosen or used + len(source.content) <= max_chars:
            chosen.add(index)
            used += len(source.content)
    return [source for index, source in enumerate(sections) if index in chosen]


async def expand_leading(
    items: Sequence[T], expand: Callable[[T], Awaitable[list[T]]], *, papers: int
) -> list[T]:
    """展开排在最前的 ``papers`` 篇，其余条目原样保留在其后。"""
    leading = await expand_in_order(items[:papers], expand, limit=papers)
    return [*leading, *items[papers:]]


async def expand_in_order(
    items: Sequence[T],
    expand: Callable[[T], Awaitable[list[R]]],
    *,
    limit: int,
    window: int = 3,
) -> list[R]:
    """按窗口展开最多 ``limit`` 个输入条目，保留它们返回的全部章节。

    ``expand`` 自行处理可预期的失败（通常回退为元数据来源）；这里不吞异常，
    未预期的异常照常向上传播。
    """
    out: list[R] = []
    selected = items[: max(0, limit)]
    step = max(1, window)
    for start in range(0, len(selected), step):
        batch = selected[start : start + step]
        expanded = await asyncio.gather(*(expand(item) for item in batch))
        for group in expanded:
            out.extend(group)
    return out
