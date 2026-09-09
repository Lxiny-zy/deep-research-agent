import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from deep_research.observability import Tracer
from deep_research.tools.base import SearchTool
from deep_research.tools.key_health import failure_kind, retry_delay, shared_health
from deep_research.tools.key_pool import ApiKeyPoolSearch


def failure(status=429, after="60"):
    response = httpx.Response(
        status, headers={"Retry-After": after}, request=httpx.Request("GET", "https://example.com")
    )
    return httpx.HTTPStatusError("secret", request=response.request, response=response)


async def test_cooldown_shared_across_runs_and_rotation_recovers(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("deep_research.tools.key_health.time.monotonic", lambda: now[0])
    calls = []
    tracer = Tracer()

    class Backend(SearchTool):
        def __init__(self, key):
            self.key = key

        async def search(self, query, *, max_results=5):
            calls.append(self.key)
            if self.key == "a" and now[0] < 160:
                raise failure()
            return []

    await ApiKeyPoolSearch("test", ["a", "b"], Backend, tracer=tracer).search("q")
    await ApiKeyPoolSearch("test", ["a", "b"], Backend).search("q")
    assert calls == ["a", "b", "b"]
    now[0] = 161
    await ApiKeyPoolSearch("test", ["a", "b"], Backend).search("q")
    assert calls[-1] == "a"
    assert "secret" not in str(tracer.events)
    assert any(e.data["status"] == "selected" for e in tracer.events)
    shared_health("test", "", "a").fail(failure(401), "invalid")
    await ApiKeyPoolSearch("test", ["rotated"], Backend).search("q")
    assert calls[-1] == "rotated"


async def test_shared_concurrency_and_waiter_cancellation_do_not_leak_slots():
    gate = asyncio.Event()
    entered = asyncio.Event()
    active = 0
    peak = 0

    class Backend(SearchTool):
        async def search(self, query, *, max_results=5):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
            try:
                await gate.wait()
                return []
            finally:
                active -= 1

    pools = [ApiKeyPoolSearch("test", ["same"], lambda _: Backend()) for _ in range(4)]
    tasks = [asyncio.create_task(p.search("q")) for p in pools]
    await asyncio.wait_for(entered.wait(), 1)
    tasks[-1].cancel()
    with pytest.raises(asyncio.CancelledError):
        await tasks[-1]
    gate.set()
    await asyncio.gather(*tasks[:-1])
    assert peak == 2
    health = shared_health("test", "", "same")
    assert health.active == health.waiting == 0


def test_retry_after_dates_and_protocol_errors():
    date = format_datetime(datetime.now(UTC) + timedelta(seconds=120))
    assert 118 <= retry_delay(failure(after=date), "limited") <= 120
    assert retry_delay(failure(after="bad"), "limited") == 30
    assert retry_delay(failure(after="nan"), "limited") == 30
    assert failure_kind(ValueError("response size limit")) is None
