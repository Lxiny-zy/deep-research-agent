"""A closed SQLite handle can retain locks until unfinished statements finalize."""

import asyncio
import gc
import sqlite3
import threading
import weakref

import aiosqlite
import pytest

from deep_research.persistence.sqlite_lifecycle import guard_sqlite_stop


@pytest.mark.parametrize("journal", ["WAL", "DELETE"])
@pytest.mark.parametrize("stop", [False, True])
async def test_close_finalizes_unread_cursor_before_releasing_native_connection(
    tmp_path, journal, stop,
):
    path = tmp_path / "cursor-close.db"
    driver = await aiosqlite.connect(path, check_same_thread=False)
    guard_sqlite_stop(driver)
    await driver.execute(f"PRAGMA journal_mode={journal}")
    await driver.execute("CREATE TABLE items (value)")
    await driver.execute("INSERT INTO items VALUES (1)")
    cursor = await driver.execute("SELECT * FROM items")
    native_cursor = cursor._cursor
    other = sqlite3.connect(path, timeout=0.03)
    try:
        if stop:
            driver.stop()
        await driver.close()
        other.execute("INSERT INTO items VALUES (2)")
        other.commit()
        assert other.execute("SELECT value FROM items").fetchall() == [(2,)]
    finally:
        # A failed baseline cannot close a cursor after its connection.close();
        # releasing references lets SQLite finalize the outstanding statement.
        del cursor, native_cursor
        gc.collect()
        other.close()
        await driver.close()


async def test_sqlalchemy_termination_releases_a_cursor_from_an_open_transaction(tmp_path):
    from sqlalchemy import text

    from deep_research.persistence.db import make_engine

    path = tmp_path / "adapter-cursor.db"
    engine = make_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    connection = await engine.connect()
    raw = await connection.get_raw_connection()
    driver, adapter = raw.driver_connection, raw.dbapi_connection
    await connection.execute(text("CREATE TABLE items (value)"))
    await connection.execute(text("INSERT INTO items VALUES (1)"))
    cursor = await driver.execute("SELECT * FROM items")
    try:
        adapter._terminate_force_close()
        await adapter._terminate_graceful_close()
        with sqlite3.connect(path, timeout=0.03) as other:
            other.execute("INSERT INTO items VALUES (2)")
            assert other.execute("SELECT value FROM items").fetchall() == [(2,)]
    finally:
        del cursor
        await connection.invalidate()
        await connection.close()
        await engine.dispose()


async def test_cyclic_gc_closes_the_cursor_even_when_an_observer_keeps_it_alive(tmp_path):
    path = tmp_path / "gc-cursor.db"
    driver = await aiosqlite.connect(path, check_same_thread=False)
    guard_sqlite_stop(driver)
    await driver.execute("PRAGMA journal_mode=WAL")
    await driver.execute("CREATE TABLE items (value)")
    await driver.execute("INSERT INTO items VALUES (1)")
    cursor = await driver.execute("SELECT * FROM items")
    native_cursor = cursor._cursor
    thread, reference = driver._thread, weakref.ref(driver)
    driver.cycle_for_test = driver
    del cursor, driver
    with pytest.warns(ResourceWarning, match="was deleted before being closed"):
        gc.collect()
    await asyncio.to_thread(thread.join, 1)
    assert reference() is None and not thread.is_alive()
    try:
        with sqlite3.connect(path, timeout=0.03) as other:
            other.execute("INSERT INTO items VALUES (2)")
    finally:
        del native_cursor
        gc.collect()


async def test_cancelled_cursor_creation_is_tracked_before_its_future_can_deliver(tmp_path):
    path = tmp_path / "cancel-cursor.db"
    driver = await aiosqlite.connect(path, check_same_thread=False)
    guard_sqlite_stop(driver)
    await driver.execute("PRAGMA journal_mode=WAL")
    await driver.execute("CREATE TABLE items (value)")
    await driver.execute("INSERT INTO items VALUES (1)")
    entered, release = threading.Event(), threading.Event()
    observers = []

    def prepare_cursor():
        cursor = driver._connection.execute("SELECT * FROM items")
        observers.append(cursor)
        entered.set()
        if not release.wait(2):
            raise RuntimeError("test did not release the worker")
        return cursor

    opening = asyncio.create_task(driver._execute(prepare_cursor))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        opening.cancel()
        await asyncio.gather(opening, return_exceptions=True)
        driver.stop()
        release.set()
        await asyncio.wait_for(driver.close(), 2)
        with sqlite3.connect(path, timeout=0.03) as other:
            other.execute("INSERT INTO items VALUES (2)")
    finally:
        release.set()
        await asyncio.gather(opening, return_exceptions=True)
        observers.clear()
        gc.collect()
        await driver.close()
