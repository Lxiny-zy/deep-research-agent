"""Tavily 检索实现（专为 AI agent 设计的搜索 API）。"""

from __future__ import annotations

from tavily import AsyncTavilyClient

from ..models import Source
from .base import SearchTool


class TavilySearch(SearchTool):
    def __init__(self, api_key: str) -> None:
        self._client = AsyncTavilyClient(api_key=api_key)

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        if max_results <= 0:
            return []
        resp = await self._client.search(query, max_results=max_results, search_depth="advanced")
        sources: list[Source] = []
        results = resp.get("results", []) if isinstance(resp, dict) else []
        for item in results if isinstance(results, list) else []:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not url:
                continue
            sources.append(
                Source(
                    title=(item.get("title") or "")[:200],
                    url=url,
                    content=(item.get("content") or "")[:2000],
                )
            )
        return sources

    async def aclose(self) -> None:
        await self._client.close()
