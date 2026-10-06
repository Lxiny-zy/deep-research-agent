"""Shared filesystem permits keep real render work bounded after lease expiry."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import BinaryIO

from .artifacts import ArtifactStore, _validate_component


def _configured_capacity() -> int:
    try:
        value = int(os.environ.get("DR_RENDER_MAX_PROCESSES", "2"))
    except ValueError as exc:
        raise ValueError("DR_RENDER_MAX_PROCESSES must be a positive integer") from exc
    if value < 1:
        raise ValueError("DR_RENDER_MAX_PROCESSES must be a positive integer")
    return value


# Every API/worker and its children must use the same deployment-level limit.
# The dispatcher and the physical file locks consume this same value.
RENDER_CAPACITY = _configured_capacity()
_guard: ContextVar[Callable[[], None] | None] = ContextVar("render_publication_guard", default=None)


def check_render_authority() -> None:
    guard = _guard.get()
    if guard is not None:
        guard()


@contextmanager
def publication_guard(check: Callable[[], None]) -> Iterator[None]:
    token = _guard.set(check)
    try:
        yield
    finally:
        _guard.reset(token)


def _try_lock(handle: BinaryIO) -> bool:
    try:
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (BlockingIOError, OSError):
        return False


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _permit(root: str | Path, names: list[str], *, timeout: float = 600) -> Iterator[None]:
    store = ArtifactStore(root, max_bytes=None)
    handles = []
    acquired = None
    deadline = time.monotonic() + timeout
    try:
        for name in names:
            path = store.control_path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                raise ValueError("render lock cannot be a symlink")
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            handles.append(os.fdopen(descriptor, "r+b"))
        while acquired is None:
            for handle in handles:
                if _try_lock(handle):
                    acquired = handle
                    break
            if acquired is None:
                if time.monotonic() >= deadline:
                    raise TimeoutError("正在等待共享渲染容量")
                time.sleep(0.02)
        yield
    finally:
        if acquired is not None:
            _unlock(acquired)
        for handle in handles:
            handle.close()


@contextmanager
def rendering_capacity(root: str | Path, *, limit: int = RENDER_CAPACITY) -> Iterator[None]:
    if limit < 1:
        raise ValueError("render capacity must be positive")
    with _permit(root, [f"render-capacity/slot-{index}.lock" for index in range(limit)]):
        yield


@contextmanager
def render_run_lock(root: str | Path, run_id: str) -> Iterator[None]:
    _validate_component(run_id, label="render run id")
    with _permit(root, [f"render-runs/{run_id}.lock"]):
        yield
