"""Admission and local capacity contracts shared by memory and SQL repositories."""

from __future__ import annotations

import asyncio

import pytest

from deep_research.config import Settings
from deep_research.execution import ExecutionContext
from deep_research.orchestration import OrchestrationRuntime
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.persistence.repository import RunQueueFullError
from deep_research.persistence.sql_repository import SqlRepository
from deep_research.worker import Worker
from tests.test_worker import _RecordingExecutor


@pytest.fixture(params=["memory", "sqlite"])
async def repo(request, tmp_path):
    if request.param == "memory":
        yield InMemoryRepository()
        return
    # Concurrent worker setup needs independent connections/transactions.
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'capacity.db').as_posix()}")
    await create_all(engine)
    try:
        yield SqlRepository(make_sessionmaker(engine))
    finally:
        await engine.dispose()


async def enqueue(repo, owner="a", workflow="deep", **kwargs):
    execution = OrchestrationRuntime().start(workflow, {"query": owner})
    execution.checkpoint = {"query": owner, "scratch": {}}
    return await repo.create_run_once(
        owner, owner_id=owner, request_hash="body", execution=execution, claimable=True, **kwargs
    )


async def test_identity_admission_preserves_other_callers_and_idempotent_replays(repo):
    limits = {"max_inflight": 4, "max_inflight_per_identity": 3}
    first, created = await enqueue(repo, idempotency_key="original", **limits)
    assert created
    for _ in range(2):
        await enqueue(repo, **limits)
    assert await enqueue(repo, idempotency_key="original", **limits) == (first, False)
    with pytest.raises(RunQueueFullError, match="身份"):
        await enqueue(repo, project_id="another-project", **limits)
    other, created = await enqueue(repo, "b", workflow="quick", **limits)
    assert other != first and created
    with pytest.raises(RunQueueFullError):
        await enqueue(repo, "c", **limits)


async def test_recovery_cannot_bypass_identity_admission(repo):
    failed, _ = await enqueue(repo)
    await repo.set_status(failed, "error")
    await enqueue(repo)
    with pytest.raises(RunQueueFullError, match="身份"):
        await repo.requeue_failed_run(failed, max_inflight=3, max_inflight_per_identity=1)
    assert await repo.get_run_status(failed) == "error"
    await enqueue(repo, "b", max_inflight=3, max_inflight_per_identity=1)


async def test_borrow_fills_unused_heavy_shares_and_keeps_the_light_slot(repo):
    for _ in range(8):
        await enqueue(repo)
    for index in range(7):
        assert (
            await repo.claim_next_run(f"w{index}", max_active_runs=8, scheduling_policy="borrow")
            is not None
        )
    assert (
        await repo.claim_next_run("full-heavy", max_active_runs=8, scheduling_policy="borrow")
        is None
    )
    light, _ = await enqueue(repo, "b", workflow="quick")
    selected = await repo.claim_next_run("light", max_active_runs=8, scheduling_policy="borrow")
    assert selected is not None and selected.run_id == light


async def test_borrow_prioritizes_a_waiting_identity_with_an_unused_share(repo):
    for _ in range(8):
        await enqueue(repo)
    for index in range(4):
        assert await repo.claim_next_run(f"w{index}", max_active_runs=8) is not None
    other, _ = await enqueue(repo, "b")
    selected = await repo.claim_next_run("next", max_active_runs=8, scheduling_policy="borrow")
    assert selected is not None and selected.run_id == other


async def test_workers_obey_different_local_limits_and_one_shared_global_limit(repo):
    for _ in range(12):
        await enqueue(repo, workflow="quick")
    released = asyncio.Event()
    workers = [
        Worker(
            repo,
            _RecordingExecutor(ExecutionContext(repo=repo), block=released),
            Settings(max_active_runs=8, worker_max_active_runs=limit),
            name=f"w{limit}",
        )
        for limit in (2, 3, 4)
    ]
    try:
        for worker in workers:
            for _ in range(5):
                await worker._tick()
        assert [len(worker._running) for worker in workers] == [2, 3, 3]
        assert [worker.capacity for worker in workers] == [0, 0, 1]
    finally:
        released.set()
        for worker in workers:
            await asyncio.gather(*worker._running)


async def test_resource_waits_do_not_trip_worker_poison_protection(repo):
    execution = OrchestrationRuntime().start("quick", {"query": "wait"})
    execution.checkpoint = {"query": "wait", "scratch": {"_resource_wait": {"count": 5}}}
    run_id, _ = await repo.create_run_once(
        "wait", request_hash="", execution=execution, claimable=True
    )
    for index in range(5):
        owner = f"previous-{index}"
        assert await repo.claim_next_run(owner) is not None
        await repo.release_lease(run_id, owner)
    executor = _RecordingExecutor(ExecutionContext(repo=repo))
    worker = Worker(repo, executor, Settings(max_claim_attempts=1))
    await worker._tick()
    await asyncio.gather(*worker._running)
    assert await repo.get_run_status(run_id) == "done"
    assert len(executor.calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_inflight_runs_per_identity", 0),
        ("worker_max_active_runs", -1),
        ("run_scheduling_policy", "unlimited"),
    ],
)
def test_invalid_scheduling_configuration_is_rejected(field, value):
    with pytest.raises(ValueError):
        Settings(**{field: value})
