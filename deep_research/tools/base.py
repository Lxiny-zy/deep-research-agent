"""检索后端抽象。可替换为 Tavily / Brave / SerpAPI / 自建向量检索。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from ..models import Source
from ..observability import Tracer

_READING_QUESTION: ContextVar[str] = ContextVar("source_reading_question", default="")
_SEARCH_TRACER: ContextVar[Tracer | None] = ContextVar("search_request_tracer", default=None)


@contextmanager
def reading_question_scope(question: str) -> Iterator[None]:
    """Keep per-question reading needs across async backend composition."""
    token = _READING_QUESTION.set(question)
    try:
        yield
    finally:
        _READING_QUESTION.reset(token)


def reading_question(search_query: str) -> str:
    return _READING_QUESTION.get() or search_query


def required_sections(question: str) -> set[str]:
    text = question.casefold()
    required: set[str] = set()
    if any(
        word in text for word in ("psnr", "ssim", "benchmark", "accuracy", "数值", "指标", "结果")
    ):
        required.add("results")
    if any(
        word in text
        for word in (
            "method",
            "approach",
            "mechanism",
            "unfold",
            "unroll",
            "prior",
            "方法",
            "机制",
            "先验",
        )
    ):
        required.add("method")
    if any(word in text for word in ("experiment", "protocol", "benchmark", "实验", "评测")):
        required.update(("experiment", "results"))
    if any(
        word in text for word in ("limitation", "conclusion", "discussion", "局限", "结论", "边界")
    ):
        required.add("conclusion")
    return required


class SearchTool(ABC):
    _default_tracer: Tracer | None = None

    @property
    def _tracer(self) -> Tracer | None:
        # Per-request ownership follows nested backends and asyncio.gather,
        # without changing a shared client's default tracer or HTTP pool.
        return _SEARCH_TRACER.get() or self._default_tracer

    @_tracer.setter
    def _tracer(self, tracer: Tracer | None) -> None:
        self._default_tracer = tracer

    def set_tracer(self, tracer: Tracer) -> None:
        self._tracer = tracer

    def for_tracer(self, tracer: Tracer) -> SearchTool:
        """Bind a run without mutating an injected/shared backend."""
        return _BoundSearchTool(self, tracer)

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


class _BoundSearchTool(SearchTool):
    def __init__(self, delegate: SearchTool, tracer: Tracer) -> None:
        self.delegate = delegate
        self._bound_tracer = tracer

    def for_tracer(self, tracer: Tracer) -> SearchTool:
        return self if tracer is self._bound_tracer else self.delegate.for_tracer(tracer)

    @property
    def backend_name(self) -> str:
        return self.delegate.backend_name

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        token = _SEARCH_TRACER.set(self._bound_tracer)
        try:
            return await self.delegate.search(query, max_results=max_results)
        finally:
            _SEARCH_TRACER.reset(token)

    async def aclose(self) -> None:
        await self.delegate.aclose()
