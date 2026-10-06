"""Drive a real embedded queue consumer in ASGI tests that omit lifespan."""

from __future__ import annotations

import asyncio

from deep_research import api


async def finish_worker_run(worker, *, seconds: float = 45) -> None:
    """Wait for normal completion before applying the independent shutdown budget.

    Worker._drain is shutdown, not an execution driver: calling it immediately
    after _tick cancels a healthy run at the 20-second shutdown grace deadline.
    Keep the test's existing 45-second execution limit and clean up afterwards.
    """
    try:
        async with asyncio.timeout(seconds):
            await asyncio.gather(*list(worker._running))
    finally:
        worker.request_stop(reason="test_complete")
        await worker._drain()
        await worker._remove_registration()


async def drain_inline(app, *, seconds: float = 45) -> None:
    consumer = api._make_inline_worker(app, app.state.settings)
    try:
        async with asyncio.timeout(seconds):
            while True:
                await consumer._tick()
                running = list(consumer._running)
                if not running:
                    break
                await asyncio.gather(*running)
    finally:
        consumer.request_stop(reason="test_complete")
        await consumer._drain()
        await consumer._remove_registration()
        if getattr(app.state, "inline_worker", None) is consumer:
            app.state.inline_worker = None
