"""Review-required completion obeys the same durable stream/attempt contract."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from deep_research.http import sse
from deep_research.observability import Event, EventHub
from deep_research.orchestration import OrchestrationRuntime
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.persistence.sql_repository import SqlRepository


@pytest.fixture(params=["memory", "sqlite"])
async def repo(request, tmp_path):
    if request.param == "memory":
        yield InMemoryRepository()
        return
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'review-stream.db').as_posix()}")
    await create_all(engine)
    try:
        yield SqlRepository(make_sessionmaker(engine))
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def fast_stream(monkeypatch):
    monkeypatch.setattr(sse, "_REMOTE_STREAM_POLL_SECONDS", 0.001)
    monkeypatch.setattr(sse, "_REMOTE_STREAM_TERMINAL_GRACE_SECONDS", 0.005)


async def _run(repo, *, status="needs_review"):
    execution = OrchestrationRuntime().start("deep", {"query": "reviewable report"})
    execution.checkpoint = {"query": "reviewable report", "scratch": {}}
    run_id = await repo.create_run("reviewable report", execution=execution, lease_owner="owner")
    await repo.set_status(run_id, status, lease_owner="owner")
    return run_id


async def _consume(repo, run_id, *, hub=None, after_seq=0):
    app = SimpleNamespace(state=SimpleNamespace(repo=repo, live={run_id: hub} if hub else {}))

    async def collect():
        frames = [frame async for frame in sse._stream_run_sse(app, run_id, after_seq=after_seq)]
        return [
            json.loads(line[6:])
            for frame in frames
            for line in frame.splitlines()
            if line.startswith("data: ")
        ]

    return await asyncio.wait_for(collect(), timeout=2)


async def test_review_terminal_replays_and_filters_an_inconsistent_done_marker(repo):
    run_id = await _run(repo)
    await repo.append_events(
        run_id,
        [
            Event(stage="SYNTHESIZER", type="report", message="draft is available"),
            Event(stage="ORCHESTRATOR", type="done", message="obsolete success"),
            Event(stage="ORCHESTRATOR", type="needs_review", message="citation needs review"),
        ],
        lease_owner="owner",
    )
    events = await _consume(repo, run_id)
    assert [event["type"] for event in events] == ["report", "needs_review"]
    assert events[-1]["message"] == "citation needs review"
    assert events[-1]["seq"] == 2
    replay = await _consume(repo, run_id, after_seq=2)
    assert [event["type"] for event in replay] == ["needs_review"]


async def test_review_terminal_closes_a_live_stream_without_waiting_for_hub_close(repo):
    run_id = await _run(repo)
    hub = EventHub()
    hub.publish(Event(stage="ORCHESTRATOR", type="needs_review", message="review live draft"))
    events = await _consume(repo, run_id, hub=hub)
    assert [event["type"] for event in events] == ["needs_review"]
    assert events[0]["message"] == "review live draft"


async def test_review_terminal_is_synthesized_when_its_event_was_not_flushed(repo):
    run_id = await _run(repo)
    events = await _consume(repo, run_id)
    assert len(events) == 1
    assert events[0]["type"] == "needs_review"
    assert events[0]["data"] == {"status": "needs_review"}
    assert events[0]["attempt"] == 1


async def test_review_terminal_waits_for_all_durable_pages(repo, monkeypatch):
    monkeypatch.setattr(sse, "_SSE_EVENT_BATCH_SIZE", 2)
    monkeypatch.setattr(sse, "_REMOTE_STREAM_TERMINAL_GRACE_SECONDS", 0)
    run_id = await _run(repo)
    await repo.append_events(
        run_id,
        [Event(stage="RESEARCHER", type="info", message=f"finding-{i}") for i in range(5)]
        + [Event(stage="ORCHESTRATOR", type="needs_review", message="durable review result")],
        lease_owner="owner",
    )
    events = await _consume(repo, run_id)
    assert len(events) == 6
    assert [event["seq"] for event in events] == list(range(6))
    assert events[-1]["message"] == "durable review result"


@pytest.mark.parametrize("local", [False, True], ids=["remote", "live"])
async def test_old_review_terminal_does_not_end_a_resumed_attempt(repo, monkeypatch, local):
    run_id = await _run(repo)
    await repo.append_events(
        run_id,
        [Event(stage="ORCHESTRATOR", type="needs_review", message="old review marker")],
        lease_owner="owner",
    )
    await repo.prepare_resume(run_id, lease_owner="owner")
    first_read = asyncio.Event()
    original = repo.get_events

    async def observe_read(*args, **kwargs):
        events = await original(*args, **kwargs)
        first_read.set()
        return events

    monkeypatch.setattr(repo, "get_events", observe_read)
    hub = EventHub() if local else None
    task = asyncio.create_task(_consume(repo, run_id, hub=hub))
    try:
        await asyncio.wait_for(first_read.wait(), timeout=1)
        await asyncio.sleep(0)
        assert not task.done()
        await repo.set_status(run_id, "needs_review", lease_owner="owner")
        stored = await repo.append_events(
            run_id,
            [Event(stage="ORCHESTRATOR", type="needs_review", message="current review marker")],
            lease_owner="owner",
        )
        if hub is not None:
            hub.publish(stored[0])
        events = await task
        assert [event["message"] for event in events] == ["current review marker"]
        assert events[0]["attempt"] == 2
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_resume_during_terminal_grace_cannot_synthesize_an_old_review_marker(
    repo, monkeypatch
):
    monkeypatch.setattr(sse, "_REMOTE_STREAM_TERMINAL_GRACE_SECONDS", 0)
    run_id = await _run(repo)
    original = repo.get_events
    reads = 0

    async def resume_between_status_and_events(*args, **kwargs):
        nonlocal reads
        reads += 1
        if reads == 2:
            await repo.prepare_resume(run_id, lease_owner="owner")
            await repo.append_events(
                run_id,
                [Event(stage="ORCHESTRATOR", type="needs_review", message="new attempt result")],
                lease_owner="owner",
            )
            await repo.set_status(run_id, "needs_review", lease_owner="owner")
            # The reader saw the old terminal status before this transaction.
            return []
        return await original(*args, **kwargs)

    monkeypatch.setattr(repo, "get_events", resume_between_status_and_events)
    events = await _consume(repo, run_id)
    assert reads >= 3
    assert len(events) == 1
    assert events[0]["attempt"] == 2
    assert events[0]["message"] == "new attempt result"
