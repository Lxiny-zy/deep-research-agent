"""Fair scheduling is committed with leases, on real independent SQL sessions."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select, update

from deep_research.orchestration import OrchestrationRuntime
from deep_research.persistence import orm
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.sql_repository import SqlRepository


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.pg)])
async def repo(request, tmp_path):
    if request.param == "postgresql":
        from tests.test_migrations_pg import _isolated_database, _run_migration

        url = os.getenv("DATABASE_URL", "")
        if not url.startswith("postgresql+asyncpg://"):
            pytest.skip("未配置 PostgreSQL DATABASE_URL")
        async with _isolated_database(url) as isolated:
            await _run_migration(isolated, "head")
            engine = make_engine(isolated)
            try:
                yield SqlRepository(make_sessionmaker(engine))
            finally:
                await engine.dispose()
        return
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'fair.db').as_posix()}")
    await create_all(engine)
    try:
        yield SqlRepository(make_sessionmaker(engine))
    finally:
        await engine.dispose()


async def enqueue(
    repo, owner, *, workflow="quick", priority=1, claimable=True, lease_owner=None, checkpoint=True
):
    execution = OrchestrationRuntime().start(workflow, {"query": owner})
    if checkpoint:
        execution.checkpoint = {"query": owner, "scratch": {"completed": ["input"]}}
    run_id, _ = await repo.create_run_once(
        owner,
        request_hash="",
        owner_id=owner,
        execution=execution,
        claimable=claimable,
        schedule_priority=priority,
        lease_owner=lease_owner,
    )
    return run_id


async def test_one_identity_cannot_hide_another_behind_a_large_queue(repo):
    for _ in range(32):
        await enqueue(repo, "a")
    other = await enqueue(repo, "b")
    first = await repo.claim_next_run("worker-1")
    assert first is not None
    second = await repo.claim_next_run("worker-2")
    assert second is not None and second.run_id == other
    assert second.dispatch["kind"] == "light" and second.dispatch["cost"] == 1
    assert second.dispatch["queue_seconds"] >= 0


async def test_scheduler_credit_survives_a_new_repository_instance(repo):
    await enqueue(repo, "a")
    await enqueue(repo, "a")
    other = await enqueue(repo, "b")
    assert await repo.claim_next_run("first-process") is not None
    restarted = SqlRepository(repo._sm)
    claimed = await restarted.claim_next_run("second-process")
    assert claimed is not None and claimed.run_id == other


async def test_heavy_capacity_does_not_block_same_identity_light_work(repo):
    heavy = await enqueue(repo, "a", workflow="deep")
    await enqueue(repo, "a", workflow="deep")
    first = await repo.claim_next_run("w1", max_active_runs=2)
    assert first is not None and first.run_id == heavy
    assert await repo.claim_next_run("w2", max_active_runs=2) is None
    light = await enqueue(repo, "a", workflow="quick")
    second = await repo.claim_next_run("w2", max_active_runs=2)
    assert second is not None and second.run_id == light
    assert await repo.claim_next_run("w3", max_active_runs=2) is None


async def test_heavy_identity_cap_leaves_heavy_capacity_for_another_owner(repo):
    for _ in range(3):
        await enqueue(repo, "a", workflow="deep")
    assert await repo.claim_next_run("w1", max_active_runs=4) is not None
    assert await repo.claim_next_run("w2", max_active_runs=4) is not None
    assert await repo.claim_next_run("w3", max_active_runs=4) is None
    other = await enqueue(repo, "b", workflow="deep")
    third = await repo.claim_next_run("w3", max_active_runs=4)
    assert third is not None and third.run_id == other


async def test_concurrent_workers_obey_total_and_heavy_limits(repo):
    for owner, workflow in [("a", "deep"), ("b", "deep"), ("c", "quick"), ("d", "quick")]:
        await enqueue(repo, owner, workflow=workflow)
    claims = await asyncio.gather(
        *(SqlRepository(repo._sm).claim_next_run(f"w{i}", max_active_runs=2) for i in range(5))
    )
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 2 and len({claim.run_id for claim in winners}) == 2
    assert sum(claim.dispatch["kind"] == "heavy" for claim in winners) <= 1


async def test_priority_changes_order_within_an_identity(repo):
    await enqueue(repo, "a", priority=0)
    urgent = await enqueue(repo, "a", priority=2)
    claimed = await repo.claim_next_run("worker")
    assert claimed is not None and claimed.run_id == urgent
    assert claimed.dispatch["priority"] == 2


async def test_waiting_low_priority_work_eventually_precedes_new_urgent_work(repo):
    old = await enqueue(repo, "a", priority=0)
    await enqueue(repo, "a", priority=2)
    async with repo._sm() as session, session.begin():
        await session.execute(
            update(orm.ResearchRun)
            .where(orm.ResearchRun.id == old)
            .values(claimable_at=datetime.now(UTC) - timedelta(seconds=90))
        )
    claimed = await repo.claim_next_run("worker")
    assert claimed is not None and claimed.run_id == old
    assert claimed.dispatch["queue_seconds"] >= 90


async def test_executing_time_does_not_age_a_crashed_attempt_ahead_of_fresh_work(repo):
    crashed = await enqueue(repo, "a", priority=0)
    first = await repo.claim_next_run("crashed-worker")
    assert first is not None and first.run_id == crashed
    urgent = await enqueue(repo, "a", priority=2)
    async with repo._sm() as session, session.begin():
        await session.execute(
            update(orm.ResearchRun)
            .where(orm.ResearchRun.id == crashed)
            .values(claimable_at=datetime.now(UTC) - timedelta(hours=1))
        )
        await session.execute(
            update(orm.WorkflowRunRow)
            .where(orm.WorkflowRunRow.research_run_id == crashed)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    claimed = await repo.claim_next_run("replacement-worker")
    assert claimed is not None and claimed.run_id == urgent


async def test_credit_write_failure_rolls_back_the_lease_and_claim_count(repo):
    run_id = await enqueue(repo, "a")
    engine = repo._sm.kw["bind"].sync_engine

    def fail_credit_write(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO scheduler_identity".upper()):
            raise RuntimeError("injected scheduler credit failure")

    event.listen(engine, "before_cursor_execute", fail_credit_write)
    try:
        with pytest.raises(RuntimeError, match="injected scheduler credit failure"):
            await repo.claim_next_run("failed-worker")
    finally:
        event.remove(engine, "before_cursor_execute", fail_credit_write)
    async with repo._sm() as session:
        run = await session.get(orm.ResearchRun, run_id)
        workflow = await session.scalar(
            select(orm.WorkflowRunRow).where(orm.WorkflowRunRow.research_run_id == run_id)
        )
        assert run.status == "pending" and run.claim_attempts == 0
        assert workflow.lease_owner is None
        assert await session.scalar(select(func.count()).select_from(orm.SchedulerStateRow)) == 0
    claimed = await repo.claim_next_run("replacement-worker")
    assert claimed is not None and claimed.run_id == run_id


@pytest.mark.parametrize(
    ("status", "checkpoint", "lease_owner", "accepted"),
    [
        ("pending", True, None, True),
        ("running", True, None, True),
        ("cancelling", True, None, False),
        ("pending", False, None, False),
        ("running", True, "live-worker", False),
    ],
)
async def test_legacy_enqueue_only_accepts_recoverable_unleased_work(
    repo, status, checkpoint, lease_owner, accepted
):
    run_id = await enqueue(
        repo, "legacy", claimable=False, checkpoint=checkpoint, lease_owner=lease_owner
    )
    await repo.set_status(run_id, status, lease_owner=lease_owner)
    assert await repo.enqueue_run(run_id) is accepted
    if accepted:
        assert not await repo.enqueue_run(run_id)
        assert await repo.claim_next_run("recovery") is not None
    else:
        assert await repo.claim_next_run("recovery") is None
