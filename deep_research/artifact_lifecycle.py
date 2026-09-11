"""Retryable cleanup and a shared filesystem quota for run artifacts."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from .blocking import run_blocking
from .config import Settings
from .persistence.repository import ResearchRepository

logger = logging.getLogger(__name__)
_QUOTA_STATE = ".artifact-quota-state.json"
_QUOTA_LOCK = ".artifact-quota.lock"
_RECONCILE_SECONDS = 60


def disk_usage(root: Path) -> int:
    total = 0
    for directory, children, files in os.walk(root, followlinks=False):
        children[:] = [name for name in children if not (Path(directory) / name).is_symlink()]
        for name in files:
            if Path(directory) == root and name.startswith(".artifact-quota"):
                continue
            path = Path(directory) / name
            if not path.is_symlink():
                with contextlib.suppress(FileNotFoundError):
                    total += path.stat().st_size
    return total


@contextlib.contextmanager
def _quota_lock(root: Path) -> Iterator[None]:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / _QUOTA_LOCK
    if lock_path.is_symlink():
        raise ValueError("artifact quota lock must not be a symlink")
    with lock_path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            lock.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _write_usage(root: Path, used: int, scanned_at: float, *, dirty: bool) -> None:
    path = root / _QUOTA_STATE
    if path.is_symlink():
        raise ValueError("artifact quota state must not be a symlink")
    fd, temporary = tempfile.mkstemp(prefix=".artifact-quota-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {"version": 1, "used": used, "scanned_at": scanned_at, "dirty": dirty}, handle
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def _read_usage(root: Path) -> tuple[int, float]:
    path = root / _QUOTA_STATE
    if path.is_symlink():
        raise ValueError("artifact quota state must not be a symlink")
    now = time.time()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        used, scanned = state["used"], state["scanned_at"]
        if (
            state.get("version") == 1
            and state.get("dirty") is False
            and type(used) is int
            and used >= 0
            and isinstance(scanned, (int, float))
            and 0 <= now - scanned < _RECONCILE_SECONDS
        ):
            return used, float(scanned)
    except (FileNotFoundError, UnicodeError, ValueError, TypeError, KeyError):
        pass
    return disk_usage(root), now


def reconcile_quota(root: Path) -> int:
    """Recount external tool output, or recover after an interrupted write."""
    root = root.resolve()
    with _quota_lock(root):
        used = disk_usage(root)
        _write_usage(root, used, time.time(), dirty=False)
        return used


@contextlib.contextmanager
def quota_allowance(root: Path, limit: int | None, destination: Path) -> Iterator[int | None]:
    if limit is None:
        yield None
        return
    root = root.resolve()
    destination = destination.resolve()
    if not destination.is_relative_to(root):
        raise ValueError("artifact quota destination escapes its root")
    with _quota_lock(root):
        previous = destination.stat().st_size if destination.exists() else 0
        used, scanned = _read_usage(root)
        # Persist the dirty marker before committing a file. If this process
        # dies, the next writer reconciles the actual files under the lock.
        _write_usage(root, used, scanned, dirty=True)
        try:
            yield max(0, limit - used + previous)
        finally:
            current = destination.stat().st_size if destination.exists() else 0
            _write_usage(root, max(0, used - previous + current), scanned, dirty=False)


def _remove_contained(root: Path, path: Path) -> None:
    resolved_root, resolved = root.resolve(), path.resolve()
    if resolved == resolved_root or not resolved.is_relative_to(resolved_root) or path.is_symlink():
        raise ValueError("refusing artifact cleanup outside its workspace")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _remove_run(root: Path, run_id: str, target: str) -> None:
    root = root.resolve()
    with _quota_lock(root):
        # Removing a whole tree can touch arbitrary files. Invalidate before
        # deletion so interruption or a partial failure cannot leave stale usage.
        _write_usage(root, 0, 0, dirty=True)
        _remove_run_unlocked(root, run_id, target)


def _remove_run_unlocked(root: Path, run_id: str, target: str) -> None:
    if target.startswith("runs/"):
        UUID(run_id)
        if target != f"runs/{run_id}":
            raise ValueError("artifact cleanup run identity mismatch")
        _remove_contained(root, root / "runs" / run_id)
    else:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", target):
            raise ValueError("invalid legacy artifact slug")
        for path in (
            root / "work" / target,
            root / "output" / target,
            root / ".framework" / "manifests" / f"{target}.json",
            root / ".framework" / "plans" / f"{target}.json",
        ):
            _remove_contained(root, path)


async def cleanup_artifacts(repo: ResearchRepository, settings: Settings) -> None:
    for run_id, target in await repo.pending_artifact_cleanup():
        try:
            if not target.startswith("runs/") and await repo.artifact_slug_in_use(target):
                # Legacy releases shared a topic folder; its last reference owns cleanup.
                await repo.finish_artifact_cleanup(run_id)
                continue
            await run_blocking(_remove_run, Path(settings.artifact_root), run_id, target)
            await repo.finish_artifact_cleanup(run_id)
        except FileNotFoundError:
            await repo.finish_artifact_cleanup(run_id)
        except Exception:
            logger.exception("artifact cleanup queued for retry: run_id=%s", run_id)
