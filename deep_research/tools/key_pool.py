"""Generic primary/backup pool for API-key based search providers."""

from __future__ import annotations

from collections.abc import Callable

from ..models import Source
from ..observability import Tracer
from .base import SearchTool
from .key_health import failure_kind, shared_health


class ApiKeyPoolSearch(SearchTool):
    def __init__(
        self,
        provider: str,
        keys: list[str],
        factory: Callable[[str], SearchTool],
        *,
        display_name: str | None = None,
        tracer: Tracer | None = None,
        namespace: str = "",
    ) -> None:
        if not keys:
            raise ValueError("API key pool cannot be empty")
        self._provider = provider
        self._display_name = display_name
        self._keys = list(keys)
        self._factory = factory
        self._clients: list[SearchTool | None] = [None] * len(keys)
        self._index = 0
        self._tracer = tracer
        self._namespace = namespace

    @property
    def backend_name(self) -> str:
        return self._display_name or f"{self._provider.title()}Search"

    @property
    def credential_count(self) -> int:
        return len(self._keys)

    def _client(self, index: int) -> SearchTool:
        if self._clients[index] is None:
            self._clients[index] = self._factory(self._keys[index])
        client = self._clients[index]
        if client is not None and self._tracer is not None:
            client.set_tracer(self._tracer)
        return self._clients[index]  # type: ignore[return-value]

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        if max_results <= 0:
            return []
        for index, key in enumerate(self._keys):
            health = shared_health(self._provider, self._namespace, key)
            if not health.ready:
                continue
            health.waiting += 1
            try:
                async with health.slots:
                    # A preceding request may have marked this credential while
                    # we waited. Skip it before issuing another paid request.
                    if not health.ready:
                        continue
                    health.active += 1
                    self._emit("检索请求开始", index, "started")
                    try:
                        result = await self._client(index).search(query, max_results=max_results)
                        if health.ready:
                            health.status = "ready"
                        if index != self._index:
                            self._emit("检索已切换至可用 Key", index, "selected")
                        self._index = index
                        self._emit(f"检索返回 {len(result)} 条来源", index, "success")
                        return result
                    except Exception as exc:
                        kind = failure_kind(exc)
                        if kind is None:
                            self._emit(
                                "检索请求失败", index, "failed", error_type=type(exc).__name__
                            )
                            raise
                        delay = health.fail(exc, kind)
                        self._emit(
                            "Key 暂时不可用，尝试其他凭据", index, kind, retry_after_seconds=delay
                        )
                    finally:
                        health.active -= 1
            finally:
                health.waiting -= 1
        # Provider exceptions can contain request headers or key-bearing URLs.
        self._emit("Key 池全部不可用或正在冷却", None, "unavailable")
        raise RuntimeError(
            f"{self._provider} Key 池全部不可用或正在冷却（{len(self._keys)} 个凭据）"
        ) from None

    def _emit(self, message: str, index: int | None, status: str, **extra: object) -> None:
        if self._tracer is not None:
            self._tracer.emit(
                "RESEARCHER",
                "info",
                f"{self.backend_name}：{message}",
                data={
                    "category": "search_key",
                    "backend": self.backend_name,
                    "key_slot": index + 1 if index is not None else None,
                    "status": status,
                    **extra,
                },
            )

    async def aclose(self) -> None:
        errors = []
        for client in self._clients:
            if client is not None:
                try:
                    await client.aclose()
                except Exception as exc:
                    errors.append(exc)
        if errors:
            raise errors[0]
