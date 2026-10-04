"""Durable recovery contracts shared by memory and independent SQLite sessions."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select

from deep_research.config import Settings
from deep_research.execution import RUN_SETTINGS_CHECKPOINT_KEY, settings_for_resume
from deep_research.orchestration import OrchestrationRuntime
from deep_research.persistence import memory_repository, orm, sql_repository
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.persistence.repository import LeaseLostError
from deep_research.persistence.sql_repository import SqlRepository


@dataclass
class _Clock:
    value: datetime

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock(datetime.now(UTC))

    class ControlledDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.value.astimezone(tz) if tz else clock.value.replace(tzinfo=None)

    monkeypatch.setattr(memory_repository, "datetime", ControlledDatetime)
    monkeypatch.setattr(sql_repository, "datetime", ControlledDatetime)
    return clock


@pytest.fixture(params=["memory", "sqlite", pytest.param("postgresql", marks=pytest.mark.pg)])
async def repo(request, tmp_path, clock):
    if request.param == "memory":
        yield InMemoryRepository()
        return
    if request.param == "postgresql":
        from tests.test_migrations_pg import _isolated_database, _run_migration

        url = os.getenv("DATABASE_URL", "")
        if not url.startswith("postgresql+asyncpg://"):
            pytest.skip("未配置 PostgreSQL DATABASE_URL")
        async with _isolated_database(url) as database_url:
            await _run_migration(database_url, "head")
            engine = make_engine(database_url)
            try:
                yield SqlRepository(make_sessionmaker(engine))
            finally:
                await engine.dispose()
        return
    # A file database gives concurrent workers distinct connections/transactions.
    # StaticPool's shared in-memory connection would not exercise that boundary.
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'recovery.db').as_posix()}")
    await create_all(engine)
    try:
        yield SqlRepository(make_sessionmaker(engine))
    finally:
        await engine.dispose()


async def _claimed(repo):
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": "durable recovery"})
    runtime.save_checkpoint(
        {
            "query": "durable recovery",
            "scratch": {
                RUN_SETTINGS_CHECKPOINT_KEY: {"max_run_seconds": 240, "max_task_seconds": 3600},
                "_runtime_metrics": {"total_tokens": 1234, "elapsed": 42.5},
                "completed_nodes": ["intent", "search"],
            },
        },
        {"name": "deep", "steps": []},
    )
    run_id, _ = await repo.create_run_once(
        "durable recovery", request_hash="", execution=execution, claimable=True
    )
    claimed = await repo.claim_next_run("original-worker")
    assert claimed is not None
    assert claimed.run_id == run_id
    return claimed


def _recovery(claimed, clock):
    execution = claimed.execution.model_copy(deep=True)
    execution.checkpoint["scratch"]["_recovery"] = {
        "count": 1,
        "reason": "provider_transient",
        "not_before": clock.value.timestamp() + 30,
    }
    return execution


async def _claimable_at(repo, run_id):
    if isinstance(repo, InMemoryRepository):
        return repo._runs[run_id].claimable_at
    async with repo._sm() as session:
        return await session.scalar(
            select(orm.ResearchRun.claimable_at).where(orm.ResearchRun.id == run_id)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("maximum", [None, 2], ids=["unlimited", "limited"])
async def test_recovery_cooldown_then_takeover_preserves_settings_and_metrics(repo, clock, maximum):
    claimed = await _claimed(repo)
    execution = _recovery(claimed, clock)
    await repo.set_status(claimed.run_id, "error", lease_owner=claimed.lease_owner)
    assert await repo.defer_run(
        claimed.run_id,
        execution,
        lease_owner=claimed.lease_owner,
        not_before=clock.value.timestamp() + 30,
    )
    detail = await repo.get_run(claimed.run_id)
    assert detail.status == "running"
    assert detail.orchestration.checkpoint == execution.checkpoint
    # The executor owns the lease until its cleanup completes.
    assert not await repo.acquire_lease(claimed.run_id, "replacement-worker")
    await repo.release_lease(claimed.run_id, claimed.lease_owner)
    assert await repo.claim_next_run("replacement-worker", max_active_runs=maximum) is None
    clock.advance(29)
    assert await repo.claim_next_run("replacement-worker", max_active_runs=maximum) is None
    clock.advance(1)
    resumed = await repo.claim_next_run("replacement-worker", max_active_runs=maximum)
    assert resumed is not None
    assert resumed.run_id == claimed.run_id
    assert resumed.resumed
    assert resumed.attempt == claimed.attempt + 1
    assert resumed.claim_attempts == claimed.claim_attempts + 1
    assert resumed.execution.checkpoint == execution.checkpoint
    restored = settings_for_resume(Settings(max_run_seconds=1), resumed.execution)
    assert restored.max_run_seconds == 240
    assert restored.max_task_seconds == 3600
    assert resumed.execution.checkpoint["scratch"]["_runtime_metrics"] == {
        "total_tokens": 1234,
        "elapsed": 42.5,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_state", ["wrong_owner", "expired", "taken_over"])
async def test_defer_run_rejects_a_stale_lease_without_mutating_state(repo, clock, owner_state):
    claimed = await _claimed(repo)
    execution = _recovery(claimed, clock)
    owner = claimed.lease_owner
    if owner_state == "wrong_owner":
        owner = "unrelated-worker"
    else:
        clock.advance(121)
        if owner_state == "taken_over":
            replacement = await repo.claim_next_run("replacement-worker")
            assert replacement is not None
    before = await repo.get_run(claimed.run_id)
    checkpoint_before = before.orchestration.checkpoint.copy()
    claimable_before = await _claimable_at(repo, claimed.run_id)

    with pytest.raises(LeaseLostError):
        await repo.defer_run(
            claimed.run_id,
            execution,
            lease_owner=owner,
            not_before=clock.value.timestamp() + 30,
        )

    after = await repo.get_run(claimed.run_id)
    assert after.status == before.status
    assert after.orchestration.checkpoint == checkpoint_before
    assert await _claimable_at(repo, claimed.run_id) == claimable_before


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelling", "cancelled", "done"])
async def test_recovery_cannot_overwrite_cancellation_or_success(repo, clock, status):
    claimed = await _claimed(repo)
    execution = _recovery(claimed, clock)
    if status == "cancelling":
        assert await repo.request_cancel(claimed.run_id) == "cancelling"
    else:
        await repo.set_status(claimed.run_id, status, lease_owner=claimed.lease_owner)
    claimable_before = await _claimable_at(repo, claimed.run_id)
    assert not await repo.defer_run(
        claimed.run_id,
        execution,
        lease_owner=claimed.lease_owner,
        not_before=clock.value.timestamp() + 30,
    )
    detail = await repo.get_run(claimed.run_id)
    assert detail.status == status
    assert detail.orchestration.checkpoint == claimed.execution.checkpoint
    assert await _claimable_at(repo, claimed.run_id) == claimable_before
    await repo.release_lease(claimed.run_id, claimed.lease_owner)
    clock.advance(31)
    assert await repo.claim_next_run("replacement-worker") is None


@pytest.mark.asyncio
async def test_cancellation_wins_a_race_with_recovery_scheduling(repo, clock):
    claimed = await _claimed(repo)
    execution = _recovery(claimed, clock)
    await asyncio.gather(
        repo.defer_run(
            claimed.run_id,
            execution,
            lease_owner=claimed.lease_owner,
            not_before=clock.value.timestamp() + 30,
        ),
        repo.request_cancel(claimed.run_id),
    )
    assert await repo.get_run_status(claimed.run_id) == "cancelling"
    await repo.release_lease(claimed.run_id, claimed.lease_owner)
    clock.advance(31)
    assert await repo.claim_next_run("replacement-worker") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("maximum", [None, 4], ids=["unlimited", "limited"])
async def test_only_one_worker_can_claim_a_cooled_down_recovery(repo, clock, maximum):
    claimed = await _claimed(repo)
    execution = _recovery(claimed, clock)
    assert await repo.defer_run(
        claimed.run_id,
        execution,
        lease_owner=claimed.lease_owner,
        not_before=clock.value.timestamp() + 30,
    )
    await repo.release_lease(claimed.run_id, claimed.lease_owner)
    clock.advance(31)
    claims = await asyncio.gather(
        *(repo.claim_next_run(f"contender-{i}", max_active_runs=maximum) for i in range(4))
    )
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert winners[0].run_id == claimed.run_id
    assert winners[0].execution.checkpoint == execution.checkpoint
    assert await repo.claim_next_run("late-contender", max_active_runs=maximum) is None


@pytest.mark.asyncio
async def test_checkpoint_write_failure_rolls_back_status_and_queue_schedule(
    repo, clock, monkeypatch
):
    claimed = await _claimed(repo)
    execution = _recovery(claimed, clock)
    await repo.set_status(claimed.run_id, "error", lease_owner=claimed.lease_owner)
    claimable_before = await _claimable_at(repo, claimed.run_id)

    def fail_copy(*args, **kwargs):
        raise RuntimeError("injected checkpoint write failure")

    def fail_checkpoint_sql(conn, cursor, statement, parameters, context, executemany):
        if (
            statement.lstrip().upper().startswith("UPDATE")
            and orm.WorkflowRunRow.__tablename__ in statement
            and "checkpoint=" in statement
        ):
            raise RuntimeError("injected checkpoint write failure")

    with monkeypatch.context() as patch:
        engine = None
        if isinstance(repo, InMemoryRepository):
            patch.setattr(type(execution), "model_copy", fail_copy)
        else:
            engine = repo._sm.kw["bind"].sync_engine
            event.listen(engine, "before_cursor_execute", fail_checkpoint_sql)
        try:
            with pytest.raises(RuntimeError, match="injected checkpoint write failure"):
                await repo.defer_run(
                    claimed.run_id,
                    execution,
                    lease_owner=claimed.lease_owner,
                    not_before=clock.value.timestamp() + 30,
                )
        finally:
            if engine is not None:
                event.remove(engine, "before_cursor_execute", fail_checkpoint_sql)

    detail = await repo.get_run(claimed.run_id)
    assert detail.status == "error"
    assert detail.orchestration.checkpoint == claimed.execution.checkpoint
    assert await _claimable_at(repo, claimed.run_id) == claimable_before
    await repo.release_lease(claimed.run_id, claimed.lease_owner)
    assert await repo.claim_next_run("replacement-worker") is None
