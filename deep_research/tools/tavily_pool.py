"""多 key 故障转移的 Tavily 检索池：主备模式。

按优先级持有多个 Tavily key；检索时从当前 key 起逐个尝试，遇配额耗尽 / 限流 /
鉴权类错误则切换到下一个 key 并记住新位置（粘滞，避免每次都从头撞已耗尽的 key）。
全部 key 都失败才向上抛出，由 Researcher 的单点错误隔离兜底。

每个 key 按需创建并缓存独立 AsyncTavilyClient（client 与 key 绑定，不可中途换 key）。
"""

from __future__ import annotations

from tavily import AsyncTavilyClient

from ..models import Source
from ..observability import EventType, Tracer
from .base import SearchTool

# 触发切换 key 的错误特征（不同 SDK/网关措辞不一，按子串宽松匹配）
_FAILOVER_HINTS = ("quota", "limit", "429", "unauthorized", "401", "403", "exhaust", "credit")


def _should_failover(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(h in msg for h in _FAILOVER_HINTS)


class TavilyKeyPoolSearch(SearchTool):
    def __init__(self, api_keys: list[str], tracer: Tracer | None = None) -> None:
        if not api_keys:
            raise ValueError("搜索 key 池为空：至少需要一个 Tavily key")
        self._keys = list(api_keys)
        self._clients: list[AsyncTavilyClient | None] = [None] * len(self._keys)
        self._idx = 0  # 当前粘滞使用的 key 下标
        self._tracer = tracer

    def _client_for(self, index: int) -> AsyncTavilyClient:
        client = self._clients[index]
        if client is None:
            client = AsyncTavilyClient(api_key=self._keys[index])
            self._clients[index] = client
        return client

    def _emit(self, type_: EventType, message: str) -> None:
        if self._tracer is not None:
            self._tracer.emit("RESEARCHER", type_, message)

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        # 竞态说明：search 会被多协程并发调用，await 期间其他协程可能已把 _idx
        # 粘滞切换到新 key。若按「入口快照 + 固定偏移」遍历，failover 瞬间的在途
        # 协程会继续按旧起点撞刚被判定耗尽的 key。因此每轮迭代都基于实时 self._idx
        # 选下一个本协程未试过的 key，让在途协程立即跟随已完成的切换；不加锁，
        # 正常并发搜索不被串行化（最坏情况只是切换瞬间少量残余请求打到旧 key）。
        n = len(self._keys)
        last_exc: Exception | None = None
        tried: set[int] = set()  # 本协程已试过的 key（防重复撞、保证终止）
        while len(tried) < n:
            i = self._idx
            if i in tried:
                # 实时下标本协程已失败过：从它顺延取第一个未试的 key
                i = next(j for off in range(1, n) if (j := (self._idx + off) % n) not in tried)
            try:
                resp = await self._client_for(i).search(
                    query, max_results=max_results, search_depth="advanced"
                )
            except Exception as e:
                last_exc = e
                tried.add(i)
                if _should_failover(e) and n > 1:
                    nxt = (i + 1) % n
                    if self._idx == i:
                        # 粘滞切换：后续请求从新 key 开始。仅当全局下标仍指向失败
                        # key 时才推进，避免并发协程把别人已完成的切换又拨回去。
                        self._idx = nxt
                    self._emit("info", f"搜索 key #{i + 1} 不可用（{e}），切换到 #{nxt + 1}")
                    continue
                raise  # 非配额类错误（如网络抖动）：交给上层重试/隔离，不浪费切换
            self._idx = i
            return _to_sources(resp)
        # 所有 key 都失败
        raise RuntimeError(f"搜索 key 池全部失败（共 {n} 个）：{last_exc}")

    async def aclose(self) -> None:
        errors: list[Exception] = []
        for client in self._clients:
            if client is None:
                continue
            try:
                await client.close()
            except Exception as exc:
                # Best-effort cleanup: a broken client must not leak the
                # remaining key clients. Re-raise after all are attempted.
                errors.append(exc)
        if errors:
            raise errors[0]


def _to_sources(resp: dict) -> list[Source]:
    sources: list[Source] = []
    for item in resp.get("results", []):
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
