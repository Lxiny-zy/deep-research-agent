"""Durable, bounded rendering requests shared by API and worker processes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from copy import deepcopy
from dataclasses import dataclass, field, fields
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .persistence.coordination import transaction_lock
from .persistence.orm import RenderJobRow


class RenderConflict(ValueError):
    pass


class RenderQueueFull(TimeoutError):
    pass


class RenderFailed(RuntimeError):
    def __init__(self, job_id: str, error: dict[str, Any]) -> None:
        super().__init__(error.get("message", "渲染任务失败"))
        self.job_id, self.error = job_id, error


@dataclass
class RenderJob:
    id: str
    key: str
    pool: str
    run_id: str
    kind: str
    payload: dict[str, Any]
    payload_hash: str
    created_at: float
    queued_at: float
    available_at: float
    status: str = "pending"
    lease_owner: str | None = None
    lease_until: float | None = None
    attempts: int = 0
    stalls: int = 0
    interrupted: bool = False
    progress_token: str = ""
    request_tokens: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


def _snapshot(row: Any) -> RenderJob:
    return RenderJob(
        **{field.name: deepcopy(getattr(row, field.name)) for field in fields(RenderJob)}
    )


def _time(now: float | None) -> float:
    return time.time() if now is None else now


def _hash(pool: str, run_id: str, kind: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps([pool, run_id, kind, payload], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _owned(row: Any, owner: str, now: float) -> bool:
    return bool(
        row is not None
        and row.status == "running"
        and row.lease_owner == owner
        and row.lease_until is not None
        and row.lease_until > now
    )


def _release(row: Any, status: str) -> None:
    row.status, row.lease_owner, row.lease_until = status, None, None


def _attach_request(row: Any, token: str | None) -> None:
    if token and token not in row.request_tokens and row.status in {"pending", "running"}:
        row.request_tokens = [*row.request_tokens, token]


def _progress(row: Any, token: str) -> bool:
    if token != row.progress_token:
        row.stalls = 0
    elif row.interrupted:
        row.stalls += 1
    row.interrupted = False
    row.progress_token = token
    if row.stalls >= 3:
        _release(row, "error")
        row.error = {"kind": "recovery", "message": "渲染连续中断且没有取得进展，已停止自动重试"}
        return False
    return True


def _failure(
    row: Any, error: dict[str, Any], retryable: bool, progress: str | None, now: float, delay: float
) -> None:
    row.interrupted = False
    row.stalls = row.stalls + 1 if progress is None or progress == row.progress_token else 0
    row.progress_token = progress or row.progress_token
    row.error = deepcopy(error)
    row.queued_at, row.available_at = now, now + delay
    _release(row, "pending" if retryable and row.stalls < 3 else "error")


class MemoryRenderQueue:
    def __init__(self) -> None:
        self.jobs: dict[str, RenderJob] = {}
        self.lock = asyncio.Lock()

    async def reserve(
        self,
        *,
        key: str,
        pool: str,
        run_id: str,
        kind: str,
        payload: dict[str, Any],
        now: float | None = None,
        restart_failed: bool = False,
        request_token: str | None = None,
        max_pending: int = 64,
    ) -> RenderJob:
        now = _time(now)
        fingerprint = _hash(pool, run_id, kind, payload)
        async with self.lock:
            job = next((job for job in self.jobs.values() if job.key == key), None)
            if job is not None:
                if job.payload_hash != fingerprint:
                    raise RenderConflict("同一渲染请求不能用于不同输入")
                if not (
                    restart_failed
                    and request_token
                    and request_token not in job.request_tokens
                    and job.status == "error"
                ):
                    _attach_request(job, request_token)
                    return _snapshot(job)
            if (
                sum(
                    job.pool == pool and job.status in {"pending", "running"}
                    for job in self.jobs.values()
                )
                >= max_pending
            ):
                raise RenderQueueFull("交付队列已满，请稍后重试")
            if job is None:
                job = RenderJob(
                    str(uuid4()),
                    key,
                    pool,
                    run_id,
                    kind,
                    deepcopy(payload),
                    fingerprint,
                    now,
                    now,
                    now,
                )
                self.jobs[job.id] = job
            else:
                job.status, job.queued_at, job.available_at = "pending", now, now
                job.stalls, job.error, job.result = 0, None, None
            _attach_request(job, request_token)
            return _snapshot(job)

    async def get(self, job_id: str) -> RenderJob | None:
        async with self.lock:
            job = self.jobs.get(job_id)
            return _snapshot(job) if job is not None else None

    async def by_key(self, key: str) -> RenderJob | None:
        async with self.lock:
            job = next((item for item in self.jobs.values() if item.key == key), None)
            return _snapshot(job) if job is not None else None

    async def state(self, job_id: str) -> dict[str, Any] | None:
        async with self.lock:
            job = self.jobs.get(job_id)
            return (
                {
                    key: deepcopy(getattr(job, key))
                    for key in ("status", "lease_until", "available_at", "result", "error")
                }
                if job
                else None
            )

    async def pending(self, pool: str, *, now: float | None = None) -> list[str]:
        now = _time(now)
        async with self.lock:
            return [
                job.id
                for job in self.jobs.values()
                if job.pool == pool
                and (
                    job.status == "pending"
                    and job.available_at <= now
                    or job.status == "running"
                    and (job.lease_until or 0) <= now
                )
            ][:64]

    async def claim(
        self,
        pool: str,
        owner: str,
        *,
        limit: int = 2,
        lease_seconds: float = 90,
        now: float | None = None,
    ) -> RenderJob | None:
        if not owner or limit < 1 or lease_seconds <= 0:
            raise ValueError("invalid render lease")
        now = _time(now)
        async with self.lock:
            jobs = [job for job in self.jobs.values() if job.pool == pool]
            for job in jobs:
                if job.status == "running" and (job.lease_until or 0) <= now:
                    _release(job, "pending")
                    job.interrupted = True
            if sum(job.status == "running" for job in jobs) >= limit:
                return None
            ready = sorted(
                (job for job in jobs if job.status == "pending" and job.available_at <= now),
                key=lambda job: (job.queued_at, job.id),
            )
            if not ready:
                return None
            job = ready[0]
            job.status, job.lease_owner, job.lease_until = "running", owner, now + lease_seconds
            job.attempts += 1
            return _snapshot(job)

    async def owned(self, job_id: str, owner: str, *, now: float | None = None) -> bool:
        async with self.lock:
            return _owned(self.jobs.get(job_id), owner, _time(now))

    async def renew(
        self, job_id: str, owner: str, *, lease_seconds: float = 90, now: float | None = None
    ) -> bool:
        now = _time(now)
        async with self.lock:
            job = self.jobs.get(job_id)
            if not _owned(job, owner, now):
                return False
            assert job is not None
            job.lease_until = now + lease_seconds
            return True

    async def progress(
        self, job_id: str, owner: str, token: str, *, now: float | None = None
    ) -> bool:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not _owned(job, owner, _time(now)):
                return False
            return _progress(job, token)

    async def finish(
        self, job_id: str, owner: str, result: dict[str, Any], *, now: float | None = None
    ) -> bool:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not _owned(job, owner, _time(now)):
                return False
            assert job is not None
            job.result, job.error = deepcopy(result), None
            _release(job, "done")
            return True

    async def fail(
        self,
        job_id: str,
        owner: str,
        error: dict[str, Any],
        *,
        retryable: bool = False,
        progress: str | None = None,
        delay: float = 0,
        now: float | None = None,
    ) -> bool:
        now = _time(now)
        async with self.lock:
            job = self.jobs.get(job_id)
            if not _owned(job, owner, now):
                return False
            _failure(job, error, retryable, progress, now, delay)
            return True

    async def cancel_run(self, run_id: str) -> None:
        async with self.lock:
            for job in self.jobs.values():
                if job.run_id == run_id and job.status in {"pending", "running"}:
                    _release(job, "cancelled")

    async def cancel(self, job_id: str) -> None:
        async with self.lock:
            job = self.jobs.get(job_id)
            if job is not None and job.status in {"pending", "running"}:
                _release(job, "cancelled")

    async def remove_run(self, run_id: str) -> None:
        async with self.lock:
            for job_id in [job.id for job in self.jobs.values() if job.run_id == run_id]:
                self.jobs.pop(job_id, None)


class SqlRenderQueue:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def reserve(
        self,
        *,
        key: str,
        pool: str,
        run_id: str,
        kind: str,
        payload: dict[str, Any],
        now: float | None = None,
        restart_failed: bool = False,
        request_token: str | None = None,
        max_pending: int = 64,
    ) -> RenderJob:
        now = _time(now)
        fingerprint = _hash(pool, run_id, kind, payload)
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, "render-queue")
            row = await session.scalar(select(RenderJobRow).where(RenderJobRow.key == key))
            if row is not None:
                if row.payload_hash != fingerprint:
                    raise RenderConflict("同一渲染请求不能用于不同输入")
                if not (
                    restart_failed
                    and request_token
                    and request_token not in row.request_tokens
                    and row.status == "error"
                ):
                    _attach_request(row, request_token)
                    return _snapshot(row)
            count = await session.scalar(
                select(func.count())
                .select_from(RenderJobRow)
                .where(RenderJobRow.pool == pool, RenderJobRow.status.in_(["pending", "running"]))
            )
            if (count or 0) >= max_pending:
                raise RenderQueueFull("交付队列已满，请稍后重试")
            if row is None:
                row = RenderJobRow(
                    key=key,
                    pool=pool,
                    run_id=run_id,
                    kind=kind,
                    payload=deepcopy(payload),
                    payload_hash=fingerprint,
                    status="pending",
                    created_at=now,
                    queued_at=now,
                    available_at=now,
                    request_tokens=[],
                )
                session.add(row)
            else:
                row.status, row.queued_at, row.available_at = "pending", now, now
                row.stalls, row.error, row.result = 0, None, None
            _attach_request(row, request_token)
            await session.flush()
            return _snapshot(row)

    async def get(self, job_id: str) -> RenderJob | None:
        async with self.sessions() as session:
            row = await session.get(RenderJobRow, job_id)
            return _snapshot(row) if row is not None else None

    async def by_key(self, key: str) -> RenderJob | None:
        async with self.sessions() as session:
            row = await session.scalar(select(RenderJobRow).where(RenderJobRow.key == key))
            return _snapshot(row) if row is not None else None

    async def state(self, job_id: str) -> dict[str, Any] | None:
        async with self.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(
                            RenderJobRow.status,
                            RenderJobRow.lease_until,
                            RenderJobRow.available_at,
                            RenderJobRow.result,
                            RenderJobRow.error,
                        ).where(RenderJobRow.id == job_id)
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row is not None else None

    async def pending(self, pool: str, *, now: float | None = None) -> list[str]:
        from sqlalchemy import and_, or_

        now = _time(now)
        async with self.sessions() as session:
            return list(
                await session.scalars(
                    select(RenderJobRow.id)
                    .where(
                        RenderJobRow.pool == pool,
                        or_(
                            and_(
                                RenderJobRow.status == "pending", RenderJobRow.available_at <= now
                            ),
                            and_(RenderJobRow.status == "running", RenderJobRow.lease_until <= now),
                        ),
                    )
                    .limit(64)
                )
            )

    async def remove_run(self, run_id: str) -> None:
        from sqlalchemy import delete

        async with self.sessions() as session, session.begin():
            await transaction_lock(session, "render-queue")
            await session.execute(delete(RenderJobRow).where(RenderJobRow.run_id == run_id))

    async def claim(
        self,
        pool: str,
        owner: str,
        *,
        limit: int = 2,
        lease_seconds: float = 90,
        now: float | None = None,
    ) -> RenderJob | None:
        if not owner or limit < 1 or lease_seconds <= 0:
            raise ValueError("invalid render lease")
        now = _time(now)
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, "render-queue")
            await session.execute(
                update(RenderJobRow)
                .where(
                    RenderJobRow.pool == pool,
                    RenderJobRow.status == "running",
                    RenderJobRow.lease_until <= now,
                )
                .values(status="pending", lease_owner=None, lease_until=None, interrupted=True)
            )
            count = await session.scalar(
                select(func.count())
                .select_from(RenderJobRow)
                .where(RenderJobRow.pool == pool, RenderJobRow.status == "running")
            )
            if (count or 0) >= limit:
                return None
            row = await session.scalar(
                select(RenderJobRow)
                .where(
                    RenderJobRow.pool == pool,
                    RenderJobRow.status == "pending",
                    RenderJobRow.available_at <= now,
                )
                .order_by(RenderJobRow.queued_at, RenderJobRow.id)
                .limit(1)
            )
            if row is None:
                return None
            row.status, row.lease_owner, row.lease_until = "running", owner, now + lease_seconds
            row.attempts += 1
            await session.flush()
            return _snapshot(row)

    async def owned(self, job_id: str, owner: str, *, now: float | None = None) -> bool:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(
                        RenderJobRow.status, RenderJobRow.lease_owner, RenderJobRow.lease_until
                    ).where(RenderJobRow.id == job_id)
                )
            ).first()
            return _owned(row, owner, _time(now))

    async def _change(self, job_id: str, owner: str, operation: Any, now: float | None) -> bool:
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, "render-queue")
            row = await session.get(RenderJobRow, job_id)
            if not _owned(row, owner, _time(now)):
                return False
            result = operation(row)
            await session.flush()
            return bool(result)

    async def renew(
        self, job_id: str, owner: str, *, lease_seconds: float = 90, now: float | None = None
    ) -> bool:
        now = _time(now)

        def renew(row: RenderJobRow) -> bool:
            row.lease_until = now + lease_seconds
            return True

        return await self._change(job_id, owner, renew, now)

    async def progress(
        self, job_id: str, owner: str, token: str, *, now: float | None = None
    ) -> bool:
        return await self._change(job_id, owner, lambda row: _progress(row, token), now)

    async def finish(
        self, job_id: str, owner: str, result: dict[str, Any], *, now: float | None = None
    ) -> bool:
        def finish(row: RenderJobRow) -> bool:
            row.result, row.error = deepcopy(result), None
            _release(row, "done")
            return True

        return await self._change(job_id, owner, finish, now)

    async def fail(
        self,
        job_id: str,
        owner: str,
        error: dict[str, Any],
        *,
        retryable: bool = False,
        progress: str | None = None,
        delay: float = 0,
        now: float | None = None,
    ) -> bool:
        now = _time(now)

        def fail(row: RenderJobRow) -> bool:
            _failure(row, error, retryable, progress, now, delay)
            return True

        return await self._change(job_id, owner, fail, now)

    async def cancel_run(self, run_id: str) -> None:
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, "render-queue")
            await session.execute(
                update(RenderJobRow)
                .where(
                    RenderJobRow.run_id == run_id, RenderJobRow.status.in_(["pending", "running"])
                )
                .values(status="cancelled", lease_owner=None, lease_until=None)
            )

    async def cancel(self, job_id: str) -> None:
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, "render-queue")
            await session.execute(
                update(RenderJobRow)
                .where(RenderJobRow.id == job_id, RenderJobRow.status.in_(["pending", "running"]))
                .values(status="cancelled", lease_owner=None, lease_until=None)
            )
