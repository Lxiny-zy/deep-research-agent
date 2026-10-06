"""Real SQL lock/pool waits must not reuse a pre-wait lease clock."""

import asyncio
from contextlib import AsyncExitStack
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from deep_research.persistence.coordination import transaction_lock
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.orm import QaMessageRow
from deep_research.workbench.qa_admission import QaLimits
from deep_research.workbench.qa_requests import SqlQaRequests
from deep_research.workbench.qa_store import SqlQaStore
from tests.test_migrations_pg import _isolated_database, _postgres_url, _run_migration


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.pg)])
async def sql_pair(request, tmp_path):
    async with AsyncExitStack() as stack:
        if request.param == "postgres":
            url = await stack.enter_async_context(_isolated_database(_postgres_url()))
            await _run_migration(url, "head")
        else:
            url = f"sqlite+aiosqlite:///{tmp_path / 'clock.db'}"
        # The first real pool also lets a test block checkout deterministically.
        first = create_async_engine(url, pool_size=1, max_overflow=0)
        blocker, competitor = make_engine(url), make_engine(url)
        try:
            if request.param == "sqlite":
                await create_all(first)
            sessions = make_sessionmaker(first)
            limits = QaLimits(active=1, active_per_owner=1, pending=4, pending_per_owner=4)
            store = SqlQaStore(sessions)
            jobs = SqlQaRequests(sessions, limits)
            other = SqlQaRequests(make_sessionmaker(competitor), limits)
            first_cid = (await store.create("owner", "first")).id
            second_cid = (await store.create("owner", "second")).id
            yield jobs, other, first, make_sessionmaker(blocker), first_cid, second_cid
        finally:
            await first.dispose()
            await blocker.dispose()
            await competitor.dispose()


def utc(value):
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


async def after_expiry(until):
    await asyncio.sleep(max(0, (utc(until) - datetime.now(UTC)).total_seconds()) + 0.04)


@pytest.mark.parametrize("operation", ["renew", "complete"])
async def test_waiting_old_owner_cannot_revive_lease_or_exceed_capacity(sql_pair, operation):
    jobs, other, engine, blockers, cid, next_cid = sql_pair
    await jobs.reserve(cid, "old", "old", {"query": "old"})
    await jobs.reserve(next_cid, "next", "next", {"query": "next"})
    assert await jobs.claim(cid, "old", "old-owner", 0.6)
    leased = await jobs.get(cid, "old")
    entered = asyncio.Event()

    async def old_write():
        entered.set()
        if operation == "renew":
            return await jobs.update(cid, "old", "old-owner", seconds=30)
        return await jobs.update(cid, "old", "old-owner", result={"answer": "late"})

    async with blockers() as holder, holder.begin():
        # This is the production coordination row lock, using an independent
        # database connection. Neither the lock nor the clock is mocked.
        await transaction_lock(holder, f"qa:{cid}")
        assert datetime.now(UTC) < utc(leased.lease_until)
        old = asyncio.create_task(old_write())
        await entered.wait()
        await after_expiry(leased.lease_until)
        assert not old.done(), "the old write must actually be waiting on SQL"
        next_claim = asyncio.create_task(other.claim(next_cid, "next", "next-owner", 30))
        if engine.dialect.name == "postgresql":
            # MVCC permits the independent conversation to claim while the old
            # owner's row remains locked; a stale renewal would exceed the cap.
            assert await asyncio.wait_for(asyncio.shield(next_claim), 3)
    assert not await asyncio.wait_for(old, 3)
    assert await asyncio.wait_for(next_claim, 3)
    async with make_sessionmaker(engine)() as session:
        live = await session.scalar(
            select(func.count()).select_from(QaMessageRow).where(
                QaMessageRow.status == "running", QaMessageRow.lease_until > datetime.now(UTC),
            )
        )
    assert live == 1
    previous = await jobs.get(cid, "old")
    assert previous.status == "error" and previous.answer == ""


async def test_claim_lease_starts_after_waiting_for_admission_lock(sql_pair):
    jobs, _, _, blockers, cid, _ = sql_pair
    await jobs.reserve(cid, "pending", "pending", {"query": "pending"})
    entered = asyncio.Event()

    async def claim():
        entered.set()
        return await jobs.claim(cid, "pending", "new-owner", 0.4)

    async with blockers() as holder, holder.begin():
        await transaction_lock(holder, "qa:admission")
        waiting = asyncio.create_task(claim())
        await entered.wait()
        await asyncio.sleep(0.5)
        assert not waiting.done()
        released_at = datetime.now(UTC)
    assert await asyncio.wait_for(waiting, 3)
    row = await jobs.get(cid, "pending")
    assert row.status == "running"
    assert (utc(row.lease_until) - released_at).total_seconds() >= 0.4


async def test_status_read_rechecks_time_after_real_pool_checkout_wait(sql_pair):
    jobs, _, engine, _, cid, _ = sql_pair
    await jobs.reserve(cid, "running", "running", {"query": "running"})
    assert await jobs.claim(cid, "running", "owner", 0.4)
    row = await jobs.get(cid, "running")
    entered = asyncio.Event()

    async def read():
        entered.set()
        return await jobs.get(cid, "running")

    async with engine.connect():
        waiting = asyncio.create_task(read())
        await entered.wait()
        await after_expiry(row.lease_until)
        assert not waiting.done()
    result = await asyncio.wait_for(waiting, 3)
    assert result.status == "error" and result.execution_owner is None
