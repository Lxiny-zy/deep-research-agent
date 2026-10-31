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

T = TypeVar("T")
R = TypeVar("R")


# 开放检索每个检索式最多读几篇全文、每篇读多少。一个子问题有多个检索式、多个学术
# 后端，不设限时单次任务要读上百篇；其余相关文献保留摘要，仍可作为证据。
SEARCH_FULLTEXT_PAPERS = 3
SEARCH_FULLTEXT_CHARS = 20_000


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
