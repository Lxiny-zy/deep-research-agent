"""Make repeated aiosqlite stop requests safe for SQLAlchemy cancellation cleanup."""

from __future__ import annotations

import threading
import weakref
from typing import Any


def guard_sqlite_stop(connection: Any) -> None:
    """Share the first stop completion instead of queuing work behind a stop sentinel.

    aiosqlite 0.22.x returns a new Future for every stop(), even when its worker
    thread has exited. SQLAlchemy can force-stop while graceful close is being
    cancelled, leaving the latter waiting forever on a second stop Future.
    Apply this only to connections owned by our engine; keep the driver intact.
    """
    stop = getattr(connection, "stop", None)
    if stop is None:
        return  # Older aiosqlite versions do not expose the stop-future API.
    try:
        original = weakref.WeakMethod(stop)
    except TypeError:
        return  # Already guarded, or a different driver implementation.
    lock = threading.Lock()
    requested = False
    completion: Any = None

    def stop_once() -> Any:
        nonlocal requested, completion
        with lock:
            if not requested:
                requested = True
                method = original()
                completion = method() if method is not None else None
            return completion

    connection.stop = stop_once
