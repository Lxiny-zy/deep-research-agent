"""Database-coordinated credential admission, expiring leases and cooldowns.

Only SHA-256 fingerprints are stored. All API and worker processes using the
same database share the limits, regardless of model or search profile name.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .persistence.coordination import transaction_lock
from .persistence.orm import ProviderLeaseRow, ProviderStateRow, RequestWindowRow
from .tools.key_health import failure_kind, retry_delay, shared_health


class ProviderBusyError(RuntimeError):
    status_code = 429

    def __init__(self, delay: float) -> None:
        self.retry_after = max(1, int(delay))
        super().__init__(f"供应商凭据正在限流或冷却，请在 {self.retry_after} 秒后重试")


def fingerprint(endpoint: str, secret: str) -> str:
    url = urlsplit(endpoint)
    port = url.port or (443 if url.scheme.lower() == "https" else 80)
    origin = f"{url.scheme.lower()}://{(url.hostname or '').lower()}:{port}"
    return hashlib.sha256(f"{origin}\0{secret}".encode()).hexdigest()


class ProviderCoordinator:
    lease_seconds = 30

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        concurrency: int = 2,
        requests_per_minute: int = 120,
        wait_seconds: float = 30,
    ) -> None:
        self.sessions = sessions
        self.concurrency = concurrency
        self.requests_per_minute = requests_per_minute
        self.wait_seconds = wait_seconds

    async def acquire(self, identity: str) -> str:
        deadline = time.monotonic() + self.wait_seconds
        while True:
            now = time.time()
            async with self.sessions() as session, session.begin():
                await transaction_lock(session, identity)
                await session.execute(
                    delete(ProviderLeaseRow).where(ProviderLeaseRow.expires_at <= now)
                )
                state = await session.get(ProviderStateRow, identity)
                if state is None:
                    state = ProviderStateRow(
                        fingerprint=identity,
                        retry_at=0,
                        window_start=now,
                        window_count=0,
                        requests=0,
                        limited=0,
                    )
                    session.add(state)
                if state.retry_at > now:
                    raise ProviderBusyError(state.retry_at - now)
                if now - state.window_start >= 60:
                    state.window_start, state.window_count = now, 0
                if state.window_count >= self.requests_per_minute:
                    raise ProviderBusyError(60 - (now - state.window_start))
                active = await session.scalar(
                    select(func.count())
                    .select_from(ProviderLeaseRow)
                    .where(ProviderLeaseRow.fingerprint == identity)
                )
                if int(active or 0) < self.concurrency:
                    lease = str(uuid4())
                    session.add(
                        ProviderLeaseRow(
                            id=lease, fingerprint=identity, expires_at=now + self.lease_seconds
                        )
                    )
                    state.window_count += 1
                    state.requests += 1
                    return lease
            if time.monotonic() >= deadline:
                raise ProviderBusyError(1)
            await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))

    async def renew(self, lease: str) -> bool:
        async with self.sessions() as session, session.begin():
            row = await session.scalar(
                update(ProviderLeaseRow)
                .where(ProviderLeaseRow.id == lease, ProviderLeaseRow.expires_at > time.time())
                .values(expires_at=time.time() + self.lease_seconds)
                .returning(ProviderLeaseRow.id)
            )
            return row is not None

    async def release(self, lease: str) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(delete(ProviderLeaseRow).where(ProviderLeaseRow.id == lease))

    async def fail(self, identity: str, error: Exception) -> None:
        kind = failure_kind(error)
        if kind is None:
            return
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, identity)
            state = await session.get(ProviderStateRow, identity)
            if state is not None:
                state.retry_at = max(
                    state.retry_at, time.time() + min(86400, retry_delay(error, kind))
                )
                state.limited += int(kind == "limited")

    async def admit_request(self, identity: str, *, limit: int, window: float) -> bool:
        key = hashlib.sha256(identity.encode()).hexdigest()
        now = time.time()
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, "api-rate-limits")
            await session.execute(
                delete(RequestWindowRow).where(RequestWindowRow.starts_at < now - max(window, 3600))
            )
            row = await session.get(RequestWindowRow, key)
            if row is None:
                row = RequestWindowRow(identity=key, starts_at=now, count=0)
                session.add(row)
            if now - row.starts_at >= window:
                row.starts_at, row.count = now, 0
            if row.count >= limit:
                return False
            row.count += 1
            return True

    @asynccontextmanager
    async def slot(self, identity: str) -> AsyncIterator[None]:
        lease = await self.acquire(identity)
        owner = asyncio.current_task()
        lost = False

        async def heartbeat() -> None:
            nonlocal lost
            try:
                while True:
                    await asyncio.sleep(self.lease_seconds / 3)
                    if not await self.renew(lease):
                        raise RuntimeError("provider lease expired")
            except asyncio.CancelledError:
                raise
            except Exception:
                lost = True
                if owner is not None:
                    owner.cancel()

        task = asyncio.create_task(heartbeat())
        try:
            yield
        except Exception as error:
            await self.fail(identity, error)
            raise
        except asyncio.CancelledError:
            if lost:
                raise ProviderBusyError(self.lease_seconds) from None
            raise
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # A cancelled request still releases its slot; a crashed process is covered by TTL.
            cleanup = asyncio.create_task(self.release(lease))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise


current_coordinator: ContextVar[ProviderCoordinator | None] = ContextVar(
    "provider_coordinator", default=None
)


def coordinator_for(repo: Any, settings: Settings) -> ProviderCoordinator | None:
    sessions = getattr(repo, "_sm", None)
    if not isinstance(sessions, async_sessionmaker):
        return None
    return ProviderCoordinator(
        sessions,
        concurrency=settings.provider_max_concurrency,
        requests_per_minute=settings.provider_requests_per_minute,
        wait_seconds=min(30, settings.request_timeout),
    )


@contextmanager
def provider_scope(coordinator: ProviderCoordinator | None) -> Iterator[None]:
    if coordinator is None:
        yield
        return
    token = current_coordinator.set(coordinator)
    try:
        yield
    finally:
        current_coordinator.reset(token)


@asynccontextmanager
async def provider_request(endpoint: str, secret: str) -> AsyncIterator[None]:
    identity = fingerprint(endpoint, secret)
    coordinator = current_coordinator.get()
    if coordinator is not None:
        async with coordinator.slot(identity):
            yield
        return
    # Direct library use without a SQL repository still shares limits across runs in this loop.
    health = shared_health("provider", "", identity)
    health.waiting += 1
    try:
        async with health.slots:
            if not health.ready:
                raise ProviderBusyError(health.retry_at - time.monotonic())
            health.active += 1
            try:
                yield
            except Exception as error:
                kind = failure_kind(error)
                if kind:
                    health.fail(error, kind)
                raise
            finally:
                health.active -= 1
    finally:
        health.waiting -= 1
