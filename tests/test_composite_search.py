"""多检索后端合并：去重口径、部分失败与 manifest 标识。

去重发生在来源策略门禁**之前**，因此这里的正确性直接决定交叉印证门禁看到几个
「独立发布方」。归一化过头（把不同页面并成一条）会漏证据，归一化不足（同一页面
留两条）会凭空造出伪双源——两个方向都要有断言。
"""

from __future__ import annotations

import asyncio

import pytest

from deep_research.models import Source
from deep_research.observability import Tracer
from deep_research.tools.base import SearchTool
from deep_research.tools.composite import MultiBackendSearch, normalize_url


class FakeBackend(SearchTool):
    def __init__(self, name: str, sources: list[Source], *, fail: Exception | None = None) -> None:
        self._name = name
        self._sources = sources
        self._fail = fail
        self.closed = False
        self.queries: list[str] = []

    @property
    def backend_name(self) -> str:
        return self._name

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        self.queries.append(query)
        await asyncio.sleep(0)
        if self._fail is not None:
            raise self._fail
        return list(self._sources)

    async def aclose(self) -> None:
        self.closed = True


def _source(url: str, title: str = "t", content: str = "c") -> Source:
    return Source(title=title, url=url, content=content)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("https://Example.com/a", "https://example.com/a"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("https://example.com/a#section", "https://example.com/a"),
        ("https://example.com/a?utm_source=x", "https://example.com/a"),
        ("https://example.com/a?b=1&utm_medium=y", "https://example.com/a?b=1"),
    ],
)
def test_equivalent_urls_normalize_together(left: str, right: str) -> None:
    assert normalize_url(left) == normalize_url(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # 末尾斜杠与路径大小写在部分站点上是不同资源，不能并
        ("https://example.com/a", "https://example.com/a/"),
        ("https://example.com/A", "https://example.com/a"),
        ("https://example.com/a?b=1", "https://example.com/a?b=2"),
        ("https://example.com/a", "https://other.com/a"),
        ("http://example.com/a", "https://example.com/a"),
    ],
)
def test_distinct_urls_stay_distinct(left: str, right: str) -> None:
    assert normalize_url(left) != normalize_url(right)


@pytest.mark.asyncio
async def test_results_from_all_backends_are_merged() -> None:
    a = FakeBackend("A", [_source("https://a.com/1")])
    b = FakeBackend("B", [_source("https://b.com/1")])

    merged = await MultiBackendSearch([a, b]).search("q", max_results=5)

    assert sorted(s.url for s in merged) == ["https://a.com/1", "https://b.com/1"]


@pytest.mark.asyncio
async def test_same_page_from_two_backends_counts_once() -> None:
    """同一页面被两个后端返回，绝不能变成两个「独立来源」。"""
    a = FakeBackend("A", [_source("https://news.com/story", content="first")])
    b = FakeBackend("B", [_source("https://NEWS.com/story?utm_source=b", content="second")])

    merged = await MultiBackendSearch([a, b]).search("q")

    assert len(merged) == 1
    assert merged[0].content == "first", "先到先得：同页保留最先返回的内容"


@pytest.mark.asyncio
async def test_one_failing_backend_does_not_block_the_others() -> None:
    a = FakeBackend("A", [_source("https://a.com/1")])
    b = FakeBackend("B", [], fail=RuntimeError("rate limited"))
    tracer = Tracer()

    merged = await MultiBackendSearch([a, b], tracer=tracer).search("q")

    assert [s.url for s in merged] == ["https://a.com/1"]
    messages = [event.message for event in tracer.events]
    assert any("B" in message for message in messages), "后端失败必须留下审计事件"


@pytest.mark.asyncio
async def test_all_backends_failing_raises() -> None:
    a = FakeBackend("A", [], fail=RuntimeError("down"))
    b = FakeBackend("B", [], fail=RuntimeError("down"))

    with pytest.raises(RuntimeError, match="所有检索后端均失败"):
        await MultiBackendSearch([a, b]).search("q")


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed_as_a_backend_failure() -> None:
    a = FakeBackend("A", [], fail=asyncio.CancelledError())
    b = FakeBackend("B", [_source("https://b.com/1")])

    with pytest.raises(asyncio.CancelledError):
        await MultiBackendSearch([a, b]).search("q")


@pytest.mark.asyncio
async def test_backends_are_queried_concurrently() -> None:
    """墙钟应约等于最慢的一个后端，而不是求和。"""

    class SlowBackend(FakeBackend):
        async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
            await asyncio.sleep(0.1)
            return list(self._sources)

    backends = [SlowBackend(f"B{i}", [_source(f"https://b{i}.com/1")]) for i in range(4)]
    started = asyncio.get_running_loop().time()
    merged = await MultiBackendSearch(list(backends)).search("q")
    elapsed = asyncio.get_running_loop().time() - started

    assert len(merged) == 4
    assert elapsed < 0.3, f"串行执行会接近 0.4s，实测 {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_backend_name_records_the_combination() -> None:
    """manifest 必须能区分「单后端跑的」与「双后端跑的」，否则无法做对照实验。"""
    combined = MultiBackendSearch([FakeBackend("Tavily", []), FakeBackend("Brave", [])])
    assert combined.backend_name == "Tavily+Brave"


@pytest.mark.asyncio
async def test_close_releases_every_backend() -> None:
    a = FakeBackend("A", [])
    b = FakeBackend("B", [])

    await MultiBackendSearch([a, b]).aclose()

    assert a.closed and b.closed


def test_at_least_one_backend_is_required() -> None:
    with pytest.raises(ValueError):
        MultiBackendSearch([])


@pytest.mark.asyncio
async def test_non_positive_max_results_short_circuits_without_querying_backends() -> None:
    backend = FakeBackend("A", [_source("https://a.com/1")])
    assert await MultiBackendSearch([backend]).search("q", max_results=0) == []
    assert backend.queries == []


def test_normalize_url_handles_invalid_ports_without_raising() -> None:
    value = "https://example.com:not-a-port/path"
    assert normalize_url(value) == value
