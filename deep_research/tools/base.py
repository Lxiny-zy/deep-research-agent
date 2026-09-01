"""检索后端抽象。可替换为 Tavily / Brave / SerpAPI / 自建向量检索。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Source


class SearchTool(ABC):
    @abstractmethod
    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        """检索并返回标准化的来源列表。"""
        raise NotImplementedError

    @property
    def backend_name(self) -> str:
        """可复现实验清单里的后端标识。

        它会进 run manifest，用来回答「当时模型看到的是哪个索引返回的结果」。
        默认取类名，多后端组合等实现应覆盖为更具体的描述。
        """
        return type(self).__name__

    async def aclose(self) -> None:
        """Release client resources when a search backend owns a connection pool."""
        return None
