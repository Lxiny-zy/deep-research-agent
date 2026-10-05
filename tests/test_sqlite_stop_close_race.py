"""A forced stop followed by graceful close must release the native write lock."""

import asyncio
import gc
import sqlite3
import threading
import weakref

import aiosqlite
import pytest

from deep_research.persistence.sqlite_lifecycle import guard_sqlite_stop


@pytest.mark.parametrize("check", ["native", "writer"])
@pytest.mark.parametrize("from_thread", [False, True])
async def test_graceful_close_after_stop_cannot_discard_the_native_transaction(
    tmp_path, check, from_thread,
):
    path = tmp_path / "stop-close.db"
    connection = await aiosqlite.connect(path, check_same_thread=False)
    guard_sqlite_stop(connection)
    await connection.execute("CREATE TABLE items (value INTEGER)")
    await connection.execute("INSERT INTO items VALUES (1)")
    native = connection._connection
    assert native.in_transaction
    entered, release = threading.Event(), threading.Event()

    def queued_operation():
        entered.set()
        if not release.wait(2):
            raise RuntimeError("test did not release the worker")

    blocked = asyncio.create_task(connection._execute(queued_operation))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        stopped = await asyncio.to_thread(connection.stop) if from_thread else connection.stop()
        closing = asyncio.create_task(connection.close())
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(blocked, 2)
        if stopped is not None:
            await asyncio.wait_for(stopped, 2)
        await asyncio.wait_for(asyncio.gather(closing, return_exceptions=True), 2)
        # Retaining an observer reference must not retain the underlying database lock.
        if check == "native":
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                _ = native.in_transaction
        else:
            with sqlite3.connect(path, timeout=0.05) as other:
                other.execute("INSERT INTO items VALUES (2)")
                assert other.execute("SELECT value FROM items").fetchall() == [(2,)]
    finally:
        release.set()
        await asyncio.gather(
            blocked, *([closing] if closing is not None else []), return_exceptions=True,
        )
        native.close()
        await connection.close()


async def test_repeated_cancellation_of_one_closer_does_not_cancel_native_shutdown(tmp_path):
    path = tmp_path / "cancel-close.db"
    connection = await aiosqlite.connect(path, check_same_thread=False)
    guard_sqlite_stop(connection)
    await connection.execute("CREATE TABLE items (value INTEGER)")
    await connection.execute("INSERT INTO items VALUES (1)")
    native = connection._connection
    entered, release = threading.Event(), threading.Event()

    def queued_operation():
        entered.set()
        if not release.wait(2):
            raise RuntimeError("test did not release the worker")

    blocked = asyncio.create_task(connection._execute(queued_operation))
    closers = []
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        first = asyncio.create_task(connection.close())
        closers.append(first)
        await asyncio.sleep(0)
        second = asyncio.create_task(connection.close())
        closers.append(second)
        first.cancel()
        await asyncio.sleep(0)
        first.cancel()
        release.set()
        await asyncio.wait_for(blocked, 2)
        results = await asyncio.wait_for(asyncio.gather(*closers, return_exceptions=True), 2)
        assert isinstance(results[0], asyncio.CancelledError) and results[1] is None
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            _ = native.in_transaction
        with sqlite3.connect(path, timeout=0.05) as other:
            other.execute("INSERT INTO items VALUES (2)")
    finally:
        release.set()
        await asyncio.gather(blocked, *closers, return_exceptions=True)
        native.close()
        await connection.close()


async def test_sqlalchemy_forced_and_graceful_termination_release_a_file_write_lock(tmp_path):
    from sqlalchemy import text

    from deep_research.persistence.db import make_engine

    path = tmp_path / "adapter-close.db"
    engine = make_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    connection = await engine.connect()
    raw = await connection.get_raw_connection()
    driver, adapter = raw.driver_connection, raw.dbapi_connection
    await connection.execute(text("CREATE TABLE items (value INTEGER)"))
    await connection.execute(text("INSERT INTO items VALUES (1)"))
    native = driver._connection
    entered, release = threading.Event(), threading.Event()

    def queued_operation():
        entered.set()
        if not release.wait(2):
            raise RuntimeError("test did not release the worker")

    blocked = asyncio.create_task(driver._execute(queued_operation))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        adapter._terminate_force_close()
        closing = asyncio.create_task(adapter._terminate_graceful_close())
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(blocked, 2)
        await asyncio.wait_for(closing, 2)
        with sqlite3.connect(path, timeout=0.05) as other:
            other.execute("INSERT INTO items VALUES (2)")
            assert other.execute("SELECT value FROM items").fetchall() == [(2,)]
    finally:
        release.set()
        await asyncio.gather(
            blocked, *([closing] if closing is not None else []), return_exceptions=True,
        )
        native.close()
        await connection.invalidate()
        await connection.close()
        await engine.dispose()


async def test_cyclic_driver_collection_still_closes_its_native_transaction(tmp_path):
    from aiosqlite.core import _STOP_RUNNING_SENTINEL

    path = tmp_path / "gc-close.db"
    connection = await aiosqlite.connect(path, check_same_thread=False)
    guard_sqlite_stop(connection)
    await connection.execute("CREATE TABLE items (value INTEGER)")
    await connection.execute("INSERT INTO items VALUES (1)")
    native, thread, queue = connection._connection, connection._thread, connection._tx
    connection.cycle_for_test = connection
    reference = weakref.ref(connection)
    try:
        del connection
        with pytest.warns(ResourceWarning, match="was deleted before being closed"):
            gc.collect()
        assert reference() is None
        await asyncio.to_thread(thread.join, 0.5)
        assert not thread.is_alive(), "GC must still queue native close and stop"
        with sqlite3.connect(path, timeout=0.05) as other:
            other.execute("INSERT INTO items VALUES (2)")
    finally:
        native.close()
        if thread.is_alive():
            queue.put_nowait((None, lambda: _STOP_RUNNING_SENTINEL))
            await asyncio.to_thread(thread.join, 2)
