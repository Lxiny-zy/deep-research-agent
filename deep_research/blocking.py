"""Bound CPU/document work without occupying the server's event loop."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar
from weakref import WeakKeyDictionary

from anyio import CapacityLimiter, to_thread

T = TypeVar("T")
_limiters: WeakKeyDictionary[asyncio.AbstractEventLoop, CapacityLimiter] = WeakKeyDictionary()


async def run_blocking(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    loop = asyncio.get_running_loop()
    limiter = _limiters.setdefault(loop, CapacityLimiter(2))
    return await to_thread.run_sync(partial(function, *args, **kwargs), limiter=limiter)
