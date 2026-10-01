"""Prove draft deltas precede completion and a disconnected client cannot cancel saving."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from deep_research.config import Settings
from deep_research.workbench import qa_api


@pytest.mark.parametrize("disconnect", [False, True])
async def test_qa_deltas_arrive_before_final_answer(monkeypatch, disconnect) -> None:
    release = asyncio.Event()
    saved: list[str] = []

    async def answer(cid, body, request, *, on_delta=None, on_event=None):  # type: ignore[no-untyped-def]
        on_event(
            {"type": "reasoning", "call_id": "test", "reasoning_delta": "provider-visible text"}
        )
        on_delta("provisional answer")
        await release.wait()
        saved.append("validated answer")
        return {"id": "m1", "answer": saved[0], "citations": []}

    monkeypatch.setattr(qa_api, "_answer", answer)
    from deep_research import api

    store = qa_api.InMemoryQaStore()
    conversation = await store.create("local", "test")
    monkeypatch.setattr(api, "_check_rate_limit", AsyncMock())
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=SimpleNamespace(qa_store=store, settings=Settings())),
        }
    )
    response = await qa_api.ask_stream(
        conversation.id, qa_api.AskRequest(query="question"), request
    )
    iterator = response.body_iterator
    assert "connected" in await anext(iterator)
    assert "event: status" in await anext(iterator)
    thinking = await asyncio.wait_for(anext(iterator), 1)
    assert "event: reasoning" in thinking and "provider-visible text" in thinking
    first = await asyncio.wait_for(anext(iterator), 1)
    assert "event: delta" in first and "provisional answer" in first
    assert saved == []
    tasks = list(request.app.state.qa_tasks)
    assert len(tasks) == 1 and not tasks[0].done()
    if disconnect:
        await iterator.aclose()
    release.set()
    if not disconnect:
        final = await asyncio.wait_for(anext(iterator), 1)
        assert "event: complete" in final and "validated answer" in final
        await iterator.aclose()
    await asyncio.wait_for(asyncio.gather(*tasks), 1)
    assert saved == ["validated answer"]


async def test_reconnect_replays_snapshot_and_shares_the_original_model_task(monkeypatch):
    from deep_research import api

    release = asyncio.Event()
    calls = 0

    async def answer(cid, body, request, *, on_delta=None, on_event=None):
        nonlocal calls
        calls += 1
        on_event({"type": "reasoning", "call_id": "one", "reasoning_delta": "thinking"})
        on_delta("draft")
        await release.wait()
        on_delta(" tail")
        return {"answer": "final", "status": "done", "tokens": 9}

    store = qa_api.InMemoryQaStore()
    cid = (await store.create("local", "test")).id
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=SimpleNamespace(qa_store=store, settings=Settings())),
        }
    )
    monkeypatch.setattr(api, "_check_rate_limit", AsyncMock())
    monkeypatch.setattr(qa_api, "_answer", answer)
    body = qa_api.AskRequest(query="q", request_id="request-one")
    response = await qa_api.ask_stream(cid, body, request)
    stream = response.body_iterator
    assert "connected" in await anext(stream)
    while "event: delta" not in await asyncio.wait_for(anext(stream), 1):
        pass
    runtime = request.app.state.qa_live_turns[(cid, body.request_id)]
    await stream.aclose()
    assert not runtime.listeners and not runtime.task.done()
    reconnected = await qa_api.ask_stream(cid, body, request)
    # Returning a response that is never consumed must not leak a subscriber.
    assert not runtime.listeners
    stream = reconnected.body_iterator
    assert "connected" in await anext(stream)
    assert '"replay": true' in await anext(stream)
    assert '"reasoning_delta": "thinking"' in await anext(stream)
    assert '"delta": "draft"' in await anext(stream)
    assert "event: status" in await anext(stream)
    release.set()
    assert '"delta": " tail"' in await asyncio.wait_for(anext(stream), 1)
    assert '"answer": "final"' in await asyncio.wait_for(anext(stream), 1)
    await stream.aclose()
    assert calls == 1 and not runtime.listeners
    assert (await store.get(cid)).messages[0].answer == "final"


def test_slow_subscriber_recovers_current_state_without_unbounded_token_queue():
    from deep_research.workbench.qa_jobs import MAX_QUEUED_EVENTS, LiveTurn

    runtime = LiveTurn()
    queue = runtime.attach()
    for i in range(MAX_QUEUED_EVENTS * 20):
        runtime.emit("delta", {"delta": str(i) + ","})
    runtime.emit("reset", {"type": "reset"})
    for _ in range(MAX_QUEUED_EVENTS * 2):
        runtime.emit("reasoning", {"call_id": "r", "reasoning_delta": "x"})
    runtime.emit("delta", {"delta": "revised"})
    assert queue.qsize() < MAX_QUEUED_EVENTS + 5
    draft, reasoning = "", ""
    while not queue.empty():
        kind, payload = queue.get_nowait()
        if kind == "reset":
            draft = ""
            if payload.get("replay"):
                reasoning = ""
        elif kind == "delta":
            draft += payload["delta"]
        elif kind == "reasoning":
            reasoning += payload["reasoning_delta"]
    assert draft == "revised" and reasoning == "x" * MAX_QUEUED_EVENTS * 2
