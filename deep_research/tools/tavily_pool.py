"""多 key 故障转移的 Tavily 检索池：主备模式。

按优先级持有多个 Tavily key；检索时从当前 key 起逐个尝试，遇配额耗尽 / 限流 /
鉴权类错误则切换到下一个 key 并记住新位置（粘滞，避免每次都从头撞已耗尽的 key）。
全部 key 都失败才向上抛出，由 Researcher 的单点错误隔离兜底。

每个 key 创建独立 AsyncTavilyClient（client 与 key 绑定，不可中途换 key）。
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
        self._clients = [AsyncTavilyClient(api_key=k) for k in self._keys]
        self._idx = 0  # 当前粘滞使用的 key 下标
        self._tracer = tracer

    def _emit(self, type_: EventType, message: str) -> None:
        if self._tracer is not None:
            self._tracer.emit("RESEARCHER", type_, message)

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        n = len(self._clients)
        last_exc: Exception | None = None
        # 从当前粘滞下标起，最多把每个 key 试一遍
        for offset in range(n):
            i = (self._idx + offset) % n
            try:
                resp = await self._clients[i].search(
                    query, max_results=max_results, search_depth="advanced"
                )
            except Exception as e:
                last_exc = e
                if _should_failover(e) and n > 1:
                    nxt = (i + 1) % n
                    self._idx = nxt  # 粘滞切换：后续请求从新 key 开始
                    self._emit("info", f"搜索 key #{i + 1} 不可用（{e}），切换到 #{nxt + 1}")
                    continue
                raise  # 非配额类错误（如网络抖动）：交给上层重试/隔离，不浪费切换
            return _to_sources(resp)
        # 所有 key 都失败
        raise RuntimeError(f"搜索 key 池全部失败（共 {n} 个）：{last_exc}")


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
