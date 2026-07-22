"""多 key 故障转移检索池测试：配额错误切 key、粘滞、全失败抛错、非配额错误不切。"""

from __future__ import annotations

import pytest

from deep_research.tools.tavily_pool import TavilyKeyPoolSearch


class _FakeClient:
    """假 Tavily client：按预设脚本对每次 search 返回结果或抛异常。"""

    def __init__(self, script: list) -> None:
        self._script = script
        self.calls = 0
        self.closed = False

    async def search(self, query, *, max_results=5, search_depth="advanced"):
        item = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self):
        self.closed = True


class _CloseFakeClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.closed = False

    async def close(self):
        self.closed = True
        if self.error is not None:
            raise self.error


def _pool(scripts: list[list]) -> TavilyKeyPoolSearch:
    pool = TavilyKeyPoolSearch(["k1", "k2", "k3"])
    pool._clients = [_FakeClient(s) for s in scripts]  # 注入假 client
    return pool


_OK = {"results": [{"url": "https://a.com", "title": "A", "content": "x"}]}


@pytest.mark.asyncio
async def test_first_key_works():
    pool = _pool([[_OK], [_OK], [_OK]])
    out = await pool.search("q")
    assert out[0].url == "https://a.com"
    assert pool._idx == 0  # 没切换


@pytest.mark.asyncio
async def test_quota_error_fails_over_to_next_key():
    pool = _pool([[Exception("quota exceeded")], [_OK], [_OK]])
    out = await pool.search("q")
    assert out[0].url == "https://a.com"
    assert pool._idx == 1  # 粘滞切到第二个 key


@pytest.mark.asyncio
async def test_failover_tries_each_key_in_order() -> None:
    pool = _pool(
        [
            [Exception("quota exceeded")],
            [_OK],
            [Exception("quota exceeded")],
        ]
    )

    out = await pool.search("q")

    assert out[0].url == "https://a.com"
    assert [client.calls for client in pool._clients] == [1, 1, 0]
    assert pool._idx == 1


@pytest.mark.asyncio
async def test_failover_is_sticky():
    # 第一个 key 配额耗尽后切到 #2；下一次检索应直接从 #2 开始，不再撞 #1
    pool = _pool([[Exception("429 rate limit")], [_OK, _OK], [_OK]])
    await pool.search("q1")
    assert pool._idx == 1
    await pool.search("q2")
    assert pool._idx == 1
    assert pool._clients[0].calls == 1  # #1 只被撞过一次


@pytest.mark.asyncio
async def test_all_keys_exhausted_raises():
    pool = _pool([[Exception("quota")], [Exception("quota")], [Exception("quota")]])
    with pytest.raises(RuntimeError, match="全部失败"):
        await pool.search("q")


@pytest.mark.asyncio
async def test_non_quota_error_does_not_failover():
    # 网络抖动类错误不应切 key（交给上层重试/隔离），直接抛出
    pool = _pool([[Exception("connection reset by peer")], [_OK], [_OK]])
    with pytest.raises(Exception, match="connection reset"):
        await pool.search("q")
    assert pool._idx == 0


@pytest.mark.asyncio
async def test_empty_pool_rejected():
    with pytest.raises(ValueError, match="key 池为空"):
        TavilyKeyPoolSearch([])


@pytest.mark.asyncio
async def test_close_attempts_all_clients_when_one_fails():
    pool = TavilyKeyPoolSearch(["k1", "k2", "k3"])
    first = _CloseFakeClient(RuntimeError("first close failed"))
    second = _CloseFakeClient()
    third = _CloseFakeClient()
    pool._clients = [first, second, third]

    with pytest.raises(RuntimeError, match="first close failed"):
        await pool.aclose()
    assert first.closed and second.closed and third.closed


@pytest.mark.asyncio
async def test_clients_are_created_lazily_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    created_keys: list[str] = []
    clients: list[_FakeClient] = []

    def create_client(*, api_key: str) -> _FakeClient:
        created_keys.append(api_key)
        client = _FakeClient([_OK, _OK])
        clients.append(client)
        return client

    monkeypatch.setattr("deep_research.tools.tavily_pool.AsyncTavilyClient", create_client)

    pool = TavilyKeyPoolSearch(["k1", "k2", "k3"])
    assert created_keys == []

    await pool.search("q1")
    await pool.search("q2")

    assert created_keys == ["k1"]
    assert clients[0].calls == 2
    assert pool._clients == [clients[0], None, None]


@pytest.mark.asyncio
async def test_later_client_construction_failure_does_not_leak_created_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _FakeClient([Exception("quota exceeded")])
    created_keys: list[str] = []

    def create_client(*, api_key: str) -> _FakeClient:
        created_keys.append(api_key)
        if api_key == "k2":
            raise RuntimeError("second client construction failed")
        return first

    monkeypatch.setattr("deep_research.tools.tavily_pool.AsyncTavilyClient", create_client)

    pool = TavilyKeyPoolSearch(["k1", "k2", "k3"])
    with pytest.raises(RuntimeError, match="second client construction failed"):
        await pool.search("q")

    assert created_keys == ["k1", "k2"]
    assert pool._clients == [first, None, None]

    await pool.aclose()
    assert first.closed
