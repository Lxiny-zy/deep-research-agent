"""Bounded reuse of identical verified model responses within one run/QA turn.

Only model responses are reused. Consumers must still apply source scope,
provenance, numeric and admission checks against the current evidence.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar, cast

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass
class _Entry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    value: BaseModel | None = None
    users: int = 0


class VerificationCache:
    def __init__(self, maximum: int = 32) -> None:
        self.maximum = maximum
        self.entries: OrderedDict[str, _Entry] = OrderedDict()

    async def get_or_compute(
        self,
        key: str,
        compute: Callable[[], Awaitable[T]],
        on_hit: Callable[[], None],
        cache_if: Callable[[T], bool] | None = None,
    ) -> T:
        entry = self.entries.get(key)
        if entry is None:
            if len(self.entries) >= self.maximum:
                disposable = next((k for k, item in self.entries.items() if not item.users), None)
                if disposable is None:
                    return await compute()
                del self.entries[disposable]
            entry = _Entry()
            self.entries[key] = entry
        self.entries.move_to_end(key)
        entry.users += 1
        try:
            # Concurrent duplicate consumers wait for one request. Cancelling
            # a waiter never cancels the owner; failed/cancelled work is not cached.
            async with entry.lock:
                if entry.value is not None:
                    on_hit()
                    return cast(T, entry.value.model_copy(deep=True))
                result = await compute()
                if cache_if is None or cache_if(result):
                    entry.value = result.model_copy(deep=True)
                return result
        finally:
            entry.users -= 1
            if entry.value is None and not entry.users:
                self.entries.pop(key, None)
