"""Shutdown must give an already-cancelled admission task time to release resources."""

import asyncio
import gc

from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.worker import Worker


async def test_stop_does_not_cancel_admission_cleanup_again_before_its_grace(settings):
    settings.worker_shutdown_grace_seconds = 1
    repo = InMemoryRepository()
    worker = Worker(repo, RunExecutor(ExecutionContext(repo=repo)), settings)
    entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    events = []

    async def tick():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            closing.set()
            try:
                await release.wait()
                events.append("resource_released")
            except asyncio.CancelledError:
                events.append("cleanup_interrupted")
                raise
            raise

    worker._tick = tick
    running = asyncio.create_task(worker._run_execution_loop())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        worker.request_stop()
        await asyncio.wait_for(closing.wait(), 1)
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(running, 2)
        assert events == ["resource_released"]
        assert not worker.requires_hard_exit
    finally:
        release.set()
        worker.request_stop()
        await asyncio.gather(running, return_exceptions=True)


async def test_cancelled_pool_ping_is_checked_in_before_admission_shutdown_returns(
    settings, tmp_path, monkeypatch,
):
    from sqlalchemy import event, text

    from deep_research.persistence.db import make_engine, make_sessionmaker
    from deep_research.persistence.sql_repository import SqlRepository

    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'ping.db').as_posix()}")
    ping_started, close_started, release_close = asyncio.Event(), asyncio.Event(), asyncio.Event()
    drivers = []

    @event.listens_for(engine.sync_engine, "connect")
    def keep_driver(connection, record):
        drivers.append(connection.driver_connection)

    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    driver = drivers[0]
    real_close = driver.close
    real_ping = engine.sync_engine.dialect.do_ping

    async def delayed_close():
        close_started.set()
        await release_close.wait()
        await real_close()

    async def pending_ping():
        ping_started.set()
        await asyncio.Event().wait()

    def ping(connection):
        if not ping_started.is_set():
            return connection.await_(pending_ping())
        return real_ping(connection)

    driver.close = delayed_close
    monkeypatch.setattr(engine.sync_engine.dialect, "do_ping", ping)
    settings.worker_shutdown_grace_seconds = 1
    repo = SqlRepository(make_sessionmaker(engine))
    worker = Worker(repo, RunExecutor(ExecutionContext(repo=repo)), settings)

    async def tick():
        # Pool checkout is the same cancellation boundary as repo.list_runs.
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return 0

    async def heartbeat():
        await asyncio.Event().wait()

    async def unregister():
        pass  # This test never registered a worker or created the service schema.

    worker._tick = tick
    worker._drain_heartbeat = heartbeat
    worker._remove_registration = unregister
    running = asyncio.create_task(worker._run_execution_loop())
    try:
        await asyncio.wait_for(ping_started.wait(), 1)
        worker.request_stop()
        await asyncio.wait_for(close_started.wait(), 1)
        await asyncio.sleep(0)
        release_close.set()
        await asyncio.wait_for(running, 2)
        assert engine.sync_engine.pool.checkedout() == 0
    finally:
        release_close.set()
        worker.request_stop()
        await asyncio.gather(running, return_exceptions=True)
        await asyncio.gather(worker._admission, return_exceptions=True)
        worker._admission = None
        gc.collect()
        await engine.dispose()
