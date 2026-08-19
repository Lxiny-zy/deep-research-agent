"""EventHub：多订阅者扇出、迟到订阅者回放非 token 历史、无人订阅时丢弃 token。"""

from __future__ import annotations

import asyncio

import pytest

from deep_research.observability import Event, EventHub, EventStreamGap


def _ev(type: str, stage: str = "ORCHESTRATOR") -> Event:
    return Event(stage=stage, type=type)


@pytest.mark.asyncio
async def test_fans_out_to_multiple_subscribers():
    """两个并发订阅者都能完整收到同一份事件流（含 token），互不抢占。"""
    hub = EventHub()

    async def collect() -> list[str]:
        return [e.type async for e in hub.stream()]

    t1 = asyncio.create_task(collect())
    t2 = asyncio.create_task(collect())
    await asyncio.sleep(0.05)  # 让两个订阅者先就位

    hub.publish(_ev("start"))
    hub.publish(_ev("token"))
    hub.publish(_ev("done"))
    hub.close()

    out1, out2 = await asyncio.gather(t1, t2)
    assert out1 == ["start", "token", "done"]
    assert out2 == ["start", "token", "done"]


@pytest.mark.asyncio
async def test_late_subscriber_replays_non_token_history():
    """迟到订阅者回放到已发生的非 token 历史（不含已错过的 token），再续收实时事件。"""
    hub = EventHub()
    hub.publish(_ev("start"))
    hub.publish(_ev("token"))  # token 不进缓冲，迟到者收不到
    hub.publish(_ev("finding", stage="RESEARCHER"))

    got: list[str] = []

    async def collect() -> None:
        async for e in hub.stream():
            got.append(e.type)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.05)
    hub.publish(_ev("done"))
    hub.close()
    await task

    assert got == ["start", "finding", "done"]


@pytest.mark.asyncio
async def test_tokens_dropped_when_no_subscriber():
    """无订阅者时 token 不在内存堆积，仅非 token 事件进缓冲。"""
    hub = EventHub()
    hub.publish(_ev("token"))
    hub.publish(_ev("token"))
    hub.publish(_ev("start"))
    hub.publish(_ev("done"))

    buffered = [e.type for e in hub._buffer]
    assert buffered == ["start", "done"]


@pytest.mark.asyncio
async def test_subscriber_after_close_replays_then_ends():
    """run 结束后才订阅：回放全部非 token 历史后立即收到结束信号。"""
    hub = EventHub()
    hub.publish(_ev("start"))
    hub.publish(_ev("done"))
    hub.close()

    got = [e.type async for e in hub.stream()]
    assert got == ["start", "done"]


@pytest.mark.asyncio
async def test_live_non_token_events_receive_stable_sequence_ids():
    """实时事件在持久化回填前也带可续传的序号。"""
    hub = EventHub()
    first = _ev("start")
    second = _ev("done")

    hub.publish(first)
    hub.publish(_ev("token"))
    hub.publish(second)
    hub.close()

    assert first.seq == 0
    assert second.seq == 1
    replayed = [event async for event in hub.stream(after_seq=1)]
    assert [event.type for event in replayed] == ["done"]


def test_resume_sequence_is_primed_by_hidden_terminal_history():
    """恢复时不回放的旧终态也必须占用序号，避免新事件复用旧 ID。"""
    hub = EventHub()
    hub.prime_sequence([Event(seq=0, stage="PLANNER", type="info")])
    hub.prime_sequence([Event(seq=1, stage="ORCHESTRATOR", type="error")])
    fresh = _ev("start")

    hub.publish(fresh)

    assert fresh.seq == 2


@pytest.mark.asyncio
async def test_slow_subscriber_gets_explicit_gap_instead_of_unbounded_queue(monkeypatch):
    hub = EventHub()
    monkeypatch.setattr(hub, "_QUEUE_MAXSIZE", 2)

    stream = hub.stream()
    consumer = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)

    # The first event is consumed by the pending anext call; subsequent events
    # fill the bounded queue and then wake the subscriber with a gap marker.
    hub.publish(_ev("info"))
    await consumer
    hub.publish(_ev("info"))
    hub.publish(_ev("info"))
    hub.publish(_ev("info"))

    with pytest.raises(EventStreamGap):
        await anext(stream)
    await stream.aclose()


@pytest.mark.asyncio
async def test_old_cursor_reports_gap_after_buffer_window_is_trimmed(monkeypatch):
    hub = EventHub()
    monkeypatch.setattr(hub, "_BUFFER_MAXSIZE", 2)
    for _ in range(3):
        hub.publish(_ev("info"))

    with pytest.raises(EventStreamGap):
        await anext(hub.stream(after_seq=0))
