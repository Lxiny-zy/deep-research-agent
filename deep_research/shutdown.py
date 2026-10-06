"""Deadline waits never wait for an uncooperative task's cancellation handler."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable


def consume(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


async def wait_until(tasks: Iterable[asyncio.Task], deadline: float) -> set[asyncio.Task]:
    tasks = set(tasks)
    if not tasks:
        return set()
    for task in tasks:
        task.add_done_callback(consume)
    # asyncio.wait, unlike wait_for/gather, does not join cancellation cleanup.
    _, pending = await asyncio.wait(tasks, timeout=max(0.0, deadline - time.monotonic()))
    return pending


def cancel(tasks: Iterable[asyncio.Task]) -> None:
    for task in tasks:
        if not task.done() and not task.cancelling():
            task.cancel()
