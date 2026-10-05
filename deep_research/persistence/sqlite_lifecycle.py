"""Make repeated aiosqlite stop requests safe for SQLAlchemy cancellation cleanup."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import weakref
from typing import Any


def guard_sqlite_stop(connection: Any) -> None:
    """Use one native-close-and-stop operation for every kind of shutdown.

    aiosqlite 0.22.x returns a new Future for every stop(), even when its worker
    thread has exited. SQLAlchemy can force-stop while graceful close is being
    cancelled, leaving the latter waiting forever on a second stop Future.
    Also avoid close() clearing _connection before a queued stop() can close it:
    that race can strand an uncommitted native transaction and its SQLite lock.
    stop() in this driver closes the native connection on its worker thread,
    after queued work, so graceful and forced shutdown can use the same path.
    Apply this only to connections owned by our engine; keep the driver intact.
    """
    stop = getattr(connection, "stop", None)
    if stop is None:
        return  # Older aiosqlite versions do not expose the stop-future API.
    try:
        weakref.WeakMethod(stop)
    except TypeError:
        return  # Already guarded, or a different driver implementation.
    native = getattr(connection, "_connection", None)
    queue = getattr(connection, "_tx", None)
    thread = getattr(connection, "_thread", None)
    if native is None or queue is None or thread is None:
        return
    from aiosqlite.core import _STOP_RUNNING_SENTINEL

    lock = threading.Lock()
    requested = False
    completion: Any = None
    reference = weakref.ref(connection)
    cursors: weakref.WeakSet[sqlite3.Cursor] = weakref.WeakSet()
    execute_reference = weakref.WeakMethod(connection._execute)

    async def execute_tracking_cursors(fn: Any, *args: Any, **kwargs: Any) -> Any:
        execute = execute_reference()
        if execute is None:
            raise ValueError("SQLite connection is no longer available")

        def tracked_operation() -> Any:
            result = fn(*args, **kwargs)
            if isinstance(result, sqlite3.Cursor):
                # Track on the worker, before a cancelled future can discard the
                # result. Weak ownership must not retain ordinary closed cursors.
                cursors.add(result)
            return result

        return await execute(tracked_operation)

    connection._execute = execute_tracking_cursors

    def close_native_and_stop() -> Any:
        nonlocal native
        # Own the native handle independently of the driver's mutable attribute.
        # Weak references are already cleared when cyclic GC invokes __del__.
        if native is not None:
            # sqlite3_close_v2 defers finalization while unread cursors survive.
            # A zombie handle can therefore keep an uncommitted transaction or
            # DELETE-journal read lock after connection.close() reports success.
            for cursor in list(cursors):
                cursor.close()
            cursors.clear()
            native.close()
            native = None
        driver = reference()
        if driver is not None:
            driver._connection = None
        return _STOP_RUNNING_SENTINEL

    def stop_once() -> Any:
        nonlocal requested, completion
        with lock:
            if not requested:
                requested = True
                driver = reference()
                if driver is not None:
                    driver._running = False
                try:
                    completion = asyncio.get_event_loop().create_future()
                except Exception:
                    completion = None
                queue.put_nowait((completion, close_native_and_stop))
            return completion

    connection.stop = stop_once

    async def close_once() -> None:
        future = stop_once()
        if future is not None and not future.cancelled():
            if future.done() or future.get_loop() is asyncio.get_running_loop():
                # Cancelling one closer must not cancel every other closer's result.
                await asyncio.shield(future)
                return
        # GC or another thread may request stop without a running event loop.
        # In that case there is no awaitable result; completion is thread exit.
        if thread.is_alive():
            await asyncio.to_thread(thread.join)

    connection.close = close_once
