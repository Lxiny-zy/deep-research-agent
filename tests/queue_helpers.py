"""Drive a real embedded queue consumer in ASGI tests that omit lifespan."""

from __future__ import annotations

import asyncio

from deep_research import api


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
