"""Bound blocking work while reserving capacity for interactive file operations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar
from weakref import WeakKeyDictionary

from anyio import CapacityLimiter, to_thread

T = TypeVar("T")
_limiters: WeakKeyDictionary[asyncio.AbstractEventLoop, CapacityLimiter] = WeakKeyDictionary()
_render_limiters: WeakKeyDictionary[asyncio.AbstractEventLoop, CapacityLimiter] = (
    WeakKeyDictionary()
)


async def run_blocking(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run ordinary blocking work without competing with document generation."""
    loop = asyncio.get_running_loop()
    limiter = _limiters.setdefault(loop, CapacityLimiter(2))
    return await to_thread.run_sync(partial(function, *args, **kwargs), limiter=limiter)


async def run_rendering(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Limit heavy document builds separately from reads and other API work.

    Each process admits at most two renders at once. Waiting renders do not
    occupy an ordinary blocking slot; a disconnected HTTP waiter also does not
    release rendering capacity before its worker thread has actually finished.
    """
    loop = asyncio.get_running_loop()
    limiter = _render_limiters.setdefault(loop, CapacityLimiter(2))
    return await to_thread.run_sync(partial(function, *args, **kwargs), limiter=limiter)
