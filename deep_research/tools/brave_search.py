"""Brave Search 检索实现。

存在的理由不是「多一个可选项」，而是**交叉印证门禁的上限**：判定「≥2 个独立发布方
支持同一论断」时，能被判定的前提是这些发布方都被检索到了。单一索引下，同源伪双源
拦得住，但「两家独立媒体都没被召回」这类漏报根本无从暴露。

直接用 httpx 调 REST 端点，不引入新依赖（httpx 已是既有依赖），因此不需要重新生成
带哈希的锁文件。
"""

from __future__ import annotations

import httpx

from ..models import Source
from .base import SearchTool

_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_MAX_RESULTS = 20


class BraveSearch(SearchTool):
    def __init__(self, api_key: str, *, timeout: float = 30.0) -> None:
        if not api_key:
            raise ValueError("BraveSearch 需要 API key")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
        )

    @property
    def backend_name(self) -> str:
        return "BraveSearch"

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        if max_results <= 0:
            return []
        requested = min(max_results, _MAX_RESULTS)
        response = await self._client.get(
            _ENDPOINT,
            params={"q": query, "count": requested},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Brave Search returned a non-object JSON payload")
        web = payload.get("web")
        results = web.get("results") if isinstance(web, dict) else []
        sources: list[Source] = []
        for item in results if isinstance(results, list) else []:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not url:
                continue
            # description 是 Brave 返回的摘要；正文抓取由上游的快照流程负责，
            # 这里保持与 Tavily 实现相同的截断口径。
            sources.append(
                Source(
                    title=(item.get("title") or "")[:200],
                    url=url,
                    content=(item.get("description") or "")[:2000],
                )
            )
            if len(sources) >= requested:
                break
        return sources

    async def aclose(self) -> None:
        await self._client.aclose()
