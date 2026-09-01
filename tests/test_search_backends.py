"""检索后端选择与 Brave 响应解析。

选择逻辑的关键性质：默认配置下行为与引入多后端之前**完全一致**（返回 None，
交给 DeepResearchAgent 自建单 Tavily），否则这次改动就悄悄改变了所有既有部署。
"""

from __future__ import annotations

import logging

import httpx
import pytest

from deep_research.config import Settings
from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.tools.brave_search import BraveSearch
from deep_research.tools.composite import MultiBackendSearch


class _Catalog:
    def __init__(self, keys: list[str]) -> None:
        self._keys = keys

    async def active_keys(self) -> list[str]:
        return self._keys


def _executor(catalog=None) -> RunExecutor:
    return RunExecutor(ExecutionContext(repo=None, catalog=catalog))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_default_settings_keep_the_previous_single_backend_behaviour() -> None:
    settings = Settings(tavily_api_key="t-key")
    assert settings.search_backends == ("tavily",)

    assert await _executor().build_search_tool(settings) is None


@pytest.mark.asyncio
async def test_key_pool_still_takes_priority_for_tavily() -> None:
    tool = await _executor(_Catalog(["k1", "k2"])).build_search_tool(Settings())
    assert tool is not None
    assert tool.backend_name == "TavilyKeyPoolSearch"
    await tool.aclose()


@pytest.mark.asyncio
async def test_two_backends_are_combined() -> None:
    settings = Settings(
        search_backends=("tavily", "brave"),
        tavily_api_key="t-key",
        brave_api_key="b-key",
    )

    tool = await _executor().build_search_tool(settings)

    assert isinstance(tool, MultiBackendSearch)
    assert tool.backend_name == "TavilySearch+BraveSearch"
    await tool.aclose()


@pytest.mark.asyncio
async def test_brave_without_a_key_degrades_instead_of_failing(caplog) -> None:
    """缺 key 应退化为单后端并留下告警，而不是让整次研究起不来。"""
    settings = Settings(search_backends=("tavily", "brave"), tavily_api_key="t-key")

    with caplog.at_level(logging.WARNING, logger="deep_research.execution"):
        tool = await _executor().build_search_tool(settings)

    assert tool is not None
    assert tool.backend_name == "TavilySearch"
    assert "BRAVE_API_KEY" in caplog.text
    await tool.aclose()


@pytest.mark.asyncio
async def test_brave_only_configuration() -> None:
    settings = Settings(search_backends=("brave",), brave_api_key="b-key")

    tool = await _executor().build_search_tool(settings)

    assert tool is not None
    assert tool.backend_name == "BraveSearch"
    await tool.aclose()


def test_unknown_backend_is_rejected_at_config_time() -> None:
    with pytest.raises(ValueError, match="未知检索后端"):
        Settings(search_backends=("tavily", "yahoo"))


def test_duplicate_backends_are_collapsed() -> None:
    assert Settings(search_backends=("tavily", "tavily")).search_backends == ("tavily",)


@pytest.mark.asyncio
async def test_brave_parses_web_results() -> None:
    payload = {
        "web": {
            "results": [
                {"title": "T1", "url": "https://a.com/1", "description": "D1"},
                {"title": "T2", "url": "https://b.com/2", "description": "D2"},
                {"title": "no url", "description": "dropped"},
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Subscription-Token"] == "b-key"
        assert request.url.params["q"] == "查询"
        return httpx.Response(200, json=payload)

    tool = BraveSearch("b-key")
    tool._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"X-Subscription-Token": "b-key"},
    )
    try:
        sources = await tool.search("查询", max_results=5)
    finally:
        await tool.aclose()

    assert [s.url for s in sources] == ["https://a.com/1", "https://b.com/2"]
    assert sources[0].title == "T1"
    assert sources[0].content == "D1"


@pytest.mark.asyncio
async def test_brave_respects_max_results() -> None:
    payload = {
        "web": {"results": [{"title": f"T{i}", "url": f"https://a.com/{i}"} for i in range(10)]}
    }
    tool = BraveSearch("b-key")
    tool._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    try:
        sources = await tool.search("q", max_results=3)
    finally:
        await tool.aclose()

    assert len(sources) == 3


@pytest.mark.asyncio
async def test_brave_raises_on_http_error() -> None:
    tool = BraveSearch("b-key")
    tool._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(429, json={}))
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await tool.search("q")
    finally:
        await tool.aclose()


def test_brave_requires_a_key() -> None:
    with pytest.raises(ValueError):
        BraveSearch("")


@pytest.mark.asyncio
async def test_brave_non_positive_max_results_does_not_issue_a_request() -> None:
    tool = BraveSearch("b-key")
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    tool._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        assert await tool.search("q", max_results=0) == []
    finally:
        await tool.aclose()
    assert not called
