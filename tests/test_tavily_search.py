"""Tavily 响应解析：上游返回形状不稳定时必须安全降级，不能把脏数据交给来源门禁。"""

from __future__ import annotations

from typing import Any

import pytest

from deep_research.tools import tavily_search
from deep_research.tools.tavily_search import TavilySearch


class _FakeClient:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    async def search(self, query: str, **kwargs: Any) -> Any:
        self.calls.append((query, kwargs))
        return self.response

    async def close(self) -> None:
        self.closed = True


def _tool(monkeypatch, response: Any) -> tuple[TavilySearch, _FakeClient]:  # type: ignore[no-untyped-def]
    fake = _FakeClient(response)
    monkeypatch.setattr(tavily_search, "AsyncTavilyClient", lambda api_key: fake)
    return TavilySearch("tvly-test-key"), fake


@pytest.mark.asyncio
async def test_parses_results_and_truncates_fields(monkeypatch):
    tool, fake = _tool(
        monkeypatch,
        {
            "results": [
                {"title": "T" * 500, "url": "https://example.org/a", "content": "c" * 5000},
                {"title": None, "url": "https://example.org/b", "content": None},
            ]
        },
    )
    sources = await tool.search("cassi unfolding", max_results=3)
    assert [s.url for s in sources] == ["https://example.org/a", "https://example.org/b"]
    assert len(sources[0].title) == 200 and len(sources[0].content) == 2000
    assert sources[1].title == "" and sources[1].content == ""
    assert fake.calls == [("cassi unfolding", {"max_results": 3, "search_depth": "advanced"})]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        None,
        "not-json",
        {"results": "oops"},
        {"results": [None, 3, {"title": "no url"}, {"url": ""}]},
    ],
)
async def test_malformed_responses_yield_no_sources(monkeypatch, response):
    tool, _ = _tool(monkeypatch, response)
    assert await tool.search("q") == []


@pytest.mark.asyncio
async def test_non_positive_limit_skips_network_and_close_releases_client(monkeypatch):
    tool, fake = _tool(monkeypatch, {"results": []})
    assert await tool.search("q", max_results=0) == []
    assert fake.calls == []
    await tool.aclose()
    assert fake.closed is True
