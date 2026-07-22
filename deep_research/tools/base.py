"""检索后端抽象。可替换为 Tavily / Bing / SerpAPI / 自建向量检索。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Source


class SearchTool(ABC):
    @abstractmethod
    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        """检索并返回标准化的来源列表。"""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release client resources when a search backend owns a connection pool."""
        return None
