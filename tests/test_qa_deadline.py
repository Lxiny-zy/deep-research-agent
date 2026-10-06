"""Provider and stream-store cancellation handlers cannot hold a turn forever."""

import asyncio
import time
from types import SimpleNamespace

import pytest

from deep_research.workbench import qa_jobs
from tests.test_qa_requests import stores as stores


@pytest.fixture(autouse=True)
def quick_deadlines(monkeypatch):
    monkeypatch.setattr(qa_jobs, "CLEANUP_SECONDS", 0.15)
    monkeypatch.setattr(qa_jobs, "HEARTBEAT_SECONDS", 0.02)
    monkeypatch.setattr(qa_jobs, "FLUSH_SECONDS", 0.01)


async def ignoring_cancel(release):
    while not release.is_set():
        try:
            await release.wait()
        except asyncio.CancelledError:
            pass


async def drain(app, release):
    release.set()
    await asyncio.gather(*list(app.state.qa_tasks), return_exceptions=True)


@pytest.mark.parametrize("stop", ["local", "remote", "deadline"])
async def test_stop_and_deadline_bound_an_uncooperative_provider(stores, stop):
    store, jobs, other, cid = stores
    await jobs.reserve(cid, "old", "old", {"query": "old"})
    assert await jobs.claim(cid, "old", "old-owner", 90)
    assert await jobs.update(cid, "old", "old-owner", result={"answer": "verified history"})
    await jobs.reserve(cid, "current", "h", {"query": "q"})
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def execute(**callbacks):
        calls.append(1)
        entered.set()
        await ignoring_cancel(release)
        callbacks["on_delta"]("late unverified draft")
        return {"answer": "late answer"}

    app = SimpleNamespace(state=SimpleNamespace())
    runtime = qa_jobs.start_turn(
        app, jobs, cid, "current", execute, 0.12 if stop == "deadline" else 5,
    )
    await entered.wait()
    started = time.monotonic()
    try:
        if stop != "deadline":
            await other.cancel(cid, "current")
        if stop == "local":
            runtime.stop_requested.set()
            runtime.work.cancel()
        message = await asyncio.wait_for(runtime.task, 1)
        assert time.monotonic() - started < 0.8
        assert message["status"] == ("error" if stop == "deadline" else "cancelled")
        assert runtime.work in app.state.qa_tasks and not runtime.work.done()
        assert len(calls) == 1
        replay = qa_jobs.start_turn(app, other, cid, "current", execute, 5)
        assert (await replay.task)["status"] == message["status"]
        assert calls == [1]
    finally:
        await drain(app, release)
    final = (await store.get(cid)).messages
    assert final[0].answer == "verified history"
    assert final[1].answer == "" and "late" not in runtime.draft


async def test_stuck_event_transaction_cannot_publish_completed_model_result(stores, monkeypatch):
    store, jobs, other, cid = stores
    await jobs.reserve(cid, "current", "h", {"query": "q"})
    entered, release = asyncio.Event(), asyncio.Event()
    append = jobs.append_events
    commits = []
    update = jobs.update

    async def append_hang(*args):
        entered.set()
        await ignoring_cancel(release)
        return await append(*args)

    async def record_update(*args, **kwargs):
        if kwargs.get("result") is not None:
            commits.append(kwargs["result"])
        return await update(*args, **kwargs)

    async def execute(**callbacks):
        callbacks["on_delta"]("draft")
        return {"answer": "verified but not committed"}

    monkeypatch.setattr(jobs, "append_events", append_hang)
    monkeypatch.setattr(jobs, "update", record_update)
    app = SimpleNamespace(state=SimpleNamespace())
    started = time.monotonic()
    runtime = qa_jobs.start_turn(app, jobs, cid, "current", execute, 0.12)
    try:
        await entered.wait()
        result = await asyncio.wait_for(runtime.task, 1)
        assert result["status"] == "error" and "超时" in result["error"]
        assert time.monotonic() - started < 0.8
        assert not commits
        assert app.state.qa_tasks, "stuck transaction remains visible to API shutdown"
    finally:
        await drain(app, release)
    assert (await other.get(cid, "current")).answer == ""
    assert await other.events(cid, "current", 0) == []


async def test_event_store_failure_stops_even_an_uncooperative_provider(stores, monkeypatch):
    _, jobs, _, cid = stores
    await jobs.reserve(cid, "current", "h", {"query": "q"})
    release = asyncio.Event()

    async def execute(**callbacks):
        callbacks["on_delta"]("draft")
        await ignoring_cancel(release)
        return {"answer": "late"}

    async def broken(*args):
        raise OSError("stream store failed")

    monkeypatch.setattr(jobs, "append_events", broken)
    app = SimpleNamespace(state=SimpleNamespace())
    runtime = qa_jobs.start_turn(app, jobs, cid, "current", execute, 5)
    try:
        message = await asyncio.wait_for(runtime.task, 1)
        assert message["status"] == "error" and "OSError" in message["error"]
        assert runtime.stop_requested.is_set()
        assert runtime.work in app.state.qa_tasks
    finally:
        await drain(app, release)


async def test_stop_interrupts_waiting_for_a_stuck_final_commit(stores, monkeypatch):
    _, jobs, other, cid = stores
    await jobs.reserve(cid, "current", "h", {"query": "q"})
    entered, release = asyncio.Event(), asyncio.Event()
    update = jobs.update

    async def delayed_commit(*args, **kwargs):
        if kwargs.get("result") is not None:
            entered.set()
            await ignoring_cancel(release)
        return await update(*args, **kwargs)

    async def execute(**callbacks):
        return {"answer": "late result"}

    monkeypatch.setattr(jobs, "update", delayed_commit)
    app = SimpleNamespace(state=SimpleNamespace())
    runtime = qa_jobs.start_turn(app, jobs, cid, "current", execute, 5)
    try:
        await entered.wait()
        await other.cancel(cid, "current")
        runtime.stop_requested.set()
        assert (await asyncio.wait_for(runtime.task, 1))["status"] == "cancelled"
        assert app.state.qa_tasks
    finally:
        await drain(app, release)
    assert (await other.get(cid, "current")).answer == ""
