"""Disconnect during an SQL read must let the stream return its connection."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import anyio
import pytest
from anyio.lowlevel import checkpoint
from sqlalchemy import event, text

from deep_research.http.sse import _stream_run_sse
from deep_research.observability import EventHub
from deep_research.persistence.db import make_engine, make_sessionmaker
from deep_research.persistence.memory_repository import InMemoryRepository


@pytest.mark.parametrize("local_hub", [False, True])
@pytest.mark.parametrize("read_method", ["get_run_attempt", "get_run_status", "get_events"])
async def test_disconnect_finishes_sql_read_and_returns_connection(
    tmp_path, monkeypatch, caplog, local_hub, read_method
):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'disconnect.sqlite'}")
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    released = threading.Event()
    read_finished = False

    @event.listens_for(engine.sync_engine, "connect")
    def configure(connection, record):
        def slow_read():
            loop.call_soon_threadsafe(started.set)
            released.wait(timeout=2)
            return 1

        connection.create_function("slow_read", 0, slow_read)

    sessions = make_sessionmaker(engine)
    repo = InMemoryRepository()
    run_id = await repo.create_run("synthetic disconnect check")
    await repo.set_status(run_id, "running")
    original = getattr(repo, read_method)

    async def delayed_read(*args, **kwargs):
        nonlocal read_finished
        async with sessions() as session:
            await session.scalar(text("SELECT slow_read()"))
        read_finished = True
        return await original(*args, **kwargs)

    monkeypatch.setattr(repo, read_method, delayed_read)
    app = SimpleNamespace(
        state=SimpleNamespace(repo=repo, live={run_id: EventHub()} if local_hub else {})
    )
    scopes: list[anyio.CancelScope] = []
    chunks: list[str] = []

    async def consume():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            async for chunk in _stream_run_sse(app, run_id):
                chunks.append(chunk)

    try:
        with anyio.fail_after(5):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(consume)
                await started.wait()
                scopes[0].cancel()
                await checkpoint()
                released.set()
        assert scopes[0].cancelled_caught
        assert read_finished, "Disconnect interrupted the SQL query or session cleanup"
        assert not chunks, "A disconnected stream emitted another frame"
        assert engine.pool.checkedout() == 0
        assert not any("connection" in record.message.lower() for record in caplog.records)
        async with sessions() as session:
            assert await session.scalar(text("SELECT 1")) == 1
    finally:
        released.set()
        await engine.dispose()


@pytest.mark.parametrize("local_hub", [False, True])
async def test_already_disconnected_stream_does_not_start_a_read(local_hub):
    class UnusedRepository:
        async def get_run_attempt(self, run_id):
            pytest.fail("Disconnected stream started a repository read")

        get_run_status = get_run_attempt

    app = SimpleNamespace(
        state=SimpleNamespace(
            repo=UnusedRepository(), live={"run": EventHub()} if local_hub else {}
        )
    )
    with anyio.CancelScope() as scope:
        scope.cancel()
        async for _ in _stream_run_sse(app, "run"):
            pytest.fail("Disconnected stream emitted a frame")
    assert scope.cancelled_caught
