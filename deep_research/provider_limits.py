"""Database-coordinated credential admission, expiring leases and cooldowns.

Only SHA-256 fingerprints are stored. All API and worker processes using the
same database share the limits, regardless of model or search profile name.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import random
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4
from weakref import WeakKeyDictionary

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .persistence.coordination import transaction_lock
from .persistence.orm import ProviderLeaseRow, ProviderStateRow, RequestWindowRow
from .tools.key_health import failure_kind, retry_delay, shared_health


class ProviderBusyError(RuntimeError):
    status_code = 429

    def __init__(self, delay: float, *, context: str = "") -> None:
        self.retry_after = max(1, math.ceil(delay))
        message = f"供应商凭据正在限流或冷却，请在 {self.retry_after} 秒后重试"
        super().__init__(f"{context}：{message}" if context else message)


def fingerprint(endpoint: str, secret: str) -> str:
    url = urlsplit(endpoint)
    port = url.port or (443 if url.scheme.lower() == "https" else 80)
    origin = f"{url.scheme.lower()}://{(url.hostname or '').lower()}:{port}"
    return hashlib.sha256(f"{origin}\0{secret}".encode()).hexdigest()


@dataclass(eq=False)
class _ProviderWaiter:
    interactive: bool
    ready: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class _WaitGate:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    waiters: int = 0
    retry_at: float = 0
    pending: list[_ProviderWaiter] = field(default_factory=list)
    interactive_streak: int = 0

    def head(self) -> _ProviderWaiter | None:
        if not self.pending:
            return None
        foreground = next((item for item in self.pending if item.interactive), None)
        background = next((item for item in self.pending if not item.interactive), None)
        # Prefer a user waiting for an answer, but keep research making progress
        # even when new interactive requests continuously arrive.
        if foreground is not None and (background is None or self.interactive_streak < 3):
            return foreground
        return background

    def wake_head(self) -> None:
        if (head := self.head()) is not None:
            head.ready.set()
        self.changed.set()


# One poller per loop, connection pool and credential, including coordinators
# created by separate executions. Entries disappear when their last waiter exits.
_wait_gates: WeakKeyDictionary = WeakKeyDictionary()


class ProviderCoordinator:
    lease_seconds = 30

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        concurrency: int = 2,
        requests_per_minute: int = 120,
        wait_seconds: float = 30,
        max_waiters: int = 128,
    ) -> None:
        self.sessions = sessions
        self.concurrency = concurrency
        self.requests_per_minute = requests_per_minute
        self.wait_seconds = wait_seconds
        self.max_waiters = max_waiters

    def _gates(self) -> dict[tuple[int, str], _WaitGate]:
        return _wait_gates.setdefault(asyncio.get_running_loop(), {})

    def _gate_key(self, identity: str) -> tuple[int, str]:
        return id(self.sessions.kw.get("bind", self.sessions)), identity

    async def acquire(self, identity: str) -> str:
        deadline = time.monotonic() + self.wait_seconds
        gates, key = self._gates(), self._gate_key(identity)
        gate = gates.setdefault(key, _WaitGate())
        if gate.waiters >= self.max_waiters:
            raise ProviderBusyError(1)
        waiter = _ProviderWaiter(current_interactive.get())
        previous = gate.head()
        gate.pending.append(waiter)
        gate.waiters += 1
        if gate.head() is not previous:
            gate.wake_head()
        try:
            backoff = 0.25
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderBusyError(1)
                if gate.retry_at > time.monotonic():
                    raise ProviderBusyError(gate.retry_at - time.monotonic())
                if gate.head() is not waiter:
                    waiter.ready.clear()
                    try:
                        await asyncio.wait_for(waiter.ready.wait(), remaining)
                    except TimeoutError:
                        raise ProviderBusyError(1) from None
                    continue
                # Only the selected waiter probes SQL. Release this short lock
                # between probes so a newly queued interactive turn can go next;
                # never preempt an already-running provider request.
                async with gate.lock:
                    if gate.head() is not waiter:
                        continue
                    gate.changed.clear()
                    try:
                        lease, earliest = await self._try_acquire(identity)
                    except ProviderBusyError as exc:
                        gate.retry_at = time.monotonic() + exc.retry_after
                        raise
                    if lease is not None:
                        gate.interactive_streak = (
                            gate.interactive_streak + 1 if waiter.interactive else 0
                        )
                        return lease
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderBusyError(1)
                delay = min(remaining, 2.0, backoff * random.uniform(0.8, 1.2))
                if earliest is not None:
                    delay = min(delay, max(0.01, earliest - time.time()))
                try:
                    await asyncio.wait_for(gate.changed.wait(), delay)
                except TimeoutError:
                    if time.monotonic() >= deadline:
                        raise ProviderBusyError(1) from None
                backoff = min(2.0, backoff * 2)
        finally:
            gate.pending.remove(waiter)
            gate.waiters -= 1
            if not gate.waiters:
                gates.pop(key, None)
            else:
                gate.wake_head()

    def _check_state(self, state: ProviderStateRow | None, now: float) -> None:
        if state is None:
            return
        if state.retry_at > now:
            raise ProviderBusyError(state.retry_at - now)
        if now - state.window_start < 60 and state.window_count >= self.requests_per_minute:
            raise ProviderBusyError(60 - (now - state.window_start))

    async def _active_leases(
        self, session: AsyncSession, identity: str, now: float
    ) -> tuple[int, float | None]:
        row = (
            await session.execute(
                select(func.count(), func.min(ProviderLeaseRow.expires_at)).where(
                    ProviderLeaseRow.fingerprint == identity, ProviderLeaseRow.expires_at > now
                )
            )
        ).one()
        return int(row[0]), row[1]

    async def _try_acquire(self, identity: str) -> tuple[str | None, float | None]:
        # An advisory read keeps occupied pools out of the write-lock path.
        async with self.sessions() as session:
            now = time.time()
            self._check_state(await session.get(ProviderStateRow, identity), now)
            active, earliest = await self._active_leases(session, identity, now)
            if active >= self.concurrency:
                return None, earliest
        # Only the transaction below grants authority: advisory reads never do.
        async with self.sessions() as session, session.begin():
            await transaction_lock(session, identity)
            now = time.time()
            state = await session.get(ProviderStateRow, identity)
            self._check_state(state, now)
            # Lock/remove expired rows before counting: a concurrent renewal
            # may still be committing. Counting first could admit an extra
            # request when that renewal makes an apparently expired lease live.
            await session.execute(
                delete(ProviderLeaseRow).where(
                    ProviderLeaseRow.fingerprint == identity, ProviderLeaseRow.expires_at <= now
                )
            )
            active, earliest = await self._active_leases(session, identity, now)
            if active >= self.concurrency:
                return None, earliest
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
            if now - state.window_start >= 60:
                state.window_start, state.window_count = now, 0
            lease = str(uuid4())
            session.add(
                ProviderLeaseRow(
                    id=lease, fingerprint=identity, expires_at=now + self.lease_seconds
                )
            )
            state.window_count += 1
            state.requests += 1
            return lease, None

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
            identity = await session.scalar(
                delete(ProviderLeaseRow)
                .where(ProviderLeaseRow.id == lease)
                .returning(ProviderLeaseRow.fingerprint)
            )
        if identity is not None:
            gate = self._gates().get(self._gate_key(identity))
            if gate is not None:
                gate.changed.set()

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
current_interactive: ContextVar[bool] = ContextVar("provider_interactive", default=False)


def coordinator_for(repo: Any, settings: Settings) -> ProviderCoordinator | None:
    sessions = getattr(repo, "_sm", None)
    if not isinstance(sessions, async_sessionmaker):
        return None
    return ProviderCoordinator(
        sessions,
        concurrency=settings.provider_max_concurrency,
        requests_per_minute=settings.provider_requests_per_minute,
        wait_seconds=min(30, settings.request_timeout),
        max_waiters=settings.provider_max_waiters,
    )


@contextmanager
def provider_scope(
    coordinator: ProviderCoordinator | None, *, interactive: bool | None = None
) -> Iterator[None]:
    token = current_coordinator.set(coordinator) if coordinator is not None else None
    priority_token = current_interactive.set(interactive) if interactive is not None else None
    try:
        yield
    finally:
        if priority_token is not None:
            current_interactive.reset(priority_token)
        if token is not None:
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
