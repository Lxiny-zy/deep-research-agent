"""Worker 领取协议：两套仓储实现必须给出相同的可观察语义。

领取的正确性完全建立在租约 fencing 之上——这里验证的是 claim 不会偷走活跃租约、
不会重复派发、并且能正确区分「首次执行」与「接管续跑」。
"""

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from deep_research.orchestration import OrchestrationRuntime
from deep_research.persistence.db import create_all, make_sessionmaker
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.persistence.sql_repository import SqlRepository


@pytest.fixture(params=["memory", "sqlite"])
async def repo(request):
    if request.param == "memory":
        yield InMemoryRepository()
        return
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    await create_all(engine)
    yield SqlRepository(make_sessionmaker(engine))
    await engine.dispose()


def _execution(query: str = "Q", *, checkpoint: dict | None = None):
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": query})
    if checkpoint is not None:
        runtime.save_checkpoint(checkpoint, {"name": "deep", "steps": []})
    return execution


async def _enqueue(repo, query: str = "Q", **kwargs) -> str:
    run_id, _ = await repo.create_run_once(
        query,
        request_hash="",
        execution=_execution(query, **kwargs),
        claimable=True,
    )
    return run_id


@pytest.mark.asyncio
async def test_claim_returns_none_when_queue_is_empty(repo):
    assert await repo.claim_next_run("worker-1") is None


@pytest.mark.asyncio
async def test_non_claimable_run_is_never_claimed(repo):
    """inline 模式创建的 run 不带 claimable_at，worker 必须视而不见。"""
    run_id, _ = await repo.create_run_once("inline", request_hash="", execution=_execution())
    assert run_id
    assert await repo.claim_next_run("worker-1") is None


@pytest.mark.asyncio
async def test_claim_marks_run_running_and_owned(repo):
    run_id = await _enqueue(repo, "claim me")

    claimed = await repo.claim_next_run("worker-1")

    assert claimed is not None
    assert claimed.run_id == run_id
    assert claimed.query == "claim me"
    assert claimed.lease_owner == "worker-1"
    assert claimed.claim_attempts == 1
    assert claimed.resumed is False
    assert await repo.get_run_status(run_id) == "running"
    # 领取即持有租约：租约写入必须已生效，否则后续 fenced 写会被拒。
    await repo.set_status(run_id, "running", lease_owner="worker-1")


@pytest.mark.asyncio
async def test_claim_never_returns_the_same_run_twice(repo):
    await _enqueue(repo, "only once")

    first = await repo.claim_next_run("worker-1")
    second = await repo.claim_next_run("worker-2")

    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_claim_skips_a_run_whose_lease_is_still_live(repo):
    run_id = await _enqueue(repo)
    assert await repo.acquire_lease(run_id, "other-worker", seconds=120)

    assert await repo.claim_next_run("worker-1") is None


@pytest.mark.asyncio
async def test_expired_lease_is_taken_over_and_advances_attempt(repo):
    """执行者崩溃后租约过期，另一个 worker 接管并续跑。"""
    run_id = await _enqueue(repo, checkpoint={"query": "Q", "scratch": {}})
    first = await repo.claim_next_run("worker-1")
    assert first is not None
    # 崩溃：既不释放租约也不置终态，只让租约到期。
    assert await repo.renew_lease(run_id, "worker-1", seconds=0)

    second = await repo.claim_next_run("worker-2")

    assert second is not None
    assert second.run_id == run_id
    assert second.resumed is True
    assert second.claim_attempts == 2
    assert second.attempt > first.attempt
    assert second.execution.checkpoint == {"query": "Q", "scratch": {}}


@pytest.mark.asyncio
async def test_terminal_run_is_not_reclaimed_after_lease_expiry(repo):
    run_id = await _enqueue(repo)
    claimed = await repo.claim_next_run("worker-1")
    assert claimed is not None
    await repo.set_status(run_id, "done", lease_owner="worker-1")
    await repo.release_lease(run_id, "worker-1")

    assert await repo.claim_next_run("worker-2") is None


@pytest.mark.asyncio
async def test_claims_are_ordered_by_enqueue_time(repo):
    first_id = await _enqueue(repo, "first")
    await asyncio.sleep(0.01)
    second_id = await _enqueue(repo, "second")

    first = await repo.claim_next_run("worker-1")
    second = await repo.claim_next_run("worker-2")

    assert first is not None and second is not None
    assert [first.run_id, second.run_id] == [first_id, second_id]


@pytest.mark.asyncio
async def test_enqueue_run_makes_an_existing_run_claimable(repo):
    """resume 在 worker 模式下把任务交还队列，而不是自己执行。"""
    run_id, _ = await repo.create_run_once("resume me", request_hash="", execution=_execution())
    assert await repo.claim_next_run("worker-1") is None

    assert await repo.enqueue_run(run_id) is True

    claimed = await repo.claim_next_run("worker-1")
    assert claimed is not None
    assert claimed.run_id == run_id

    assert await repo.enqueue_run(run_id) is False


@pytest.mark.asyncio
async def test_enqueue_run_rejects_missing_and_terminal_runs(repo):
    assert await repo.enqueue_run("does-not-exist") is False
    run_id, _ = await repo.create_run_once("finished", request_hash="", execution=_execution())
    await repo.set_status(run_id, "done")
    assert await repo.enqueue_run(run_id) is False


@pytest.mark.asyncio
async def test_requeue_failed_run_requires_error_checkpoint_and_free_lease(repo):
    assert await repo.requeue_failed_run("does-not-exist") is False

    without_checkpoint = await repo.create_run("no checkpoint", execution=_execution())
    await repo.set_status(without_checkpoint, "error")
    assert await repo.requeue_failed_run(without_checkpoint) is False

    active = await repo.create_run(
        "leased failure",
        execution=_execution(checkpoint={"query": "leased failure", "scratch": {}}),
    )
    await repo.set_status(active, "error")
    assert await repo.acquire_lease(active, "current-worker", seconds=120)
    assert await repo.requeue_failed_run(active) is False
    assert await repo.get_run_status(active) == "error"


@pytest.mark.asyncio
async def test_requeue_failed_run_is_atomic_and_claimable(repo):
    run_id = await repo.create_run(
        "failed with checkpoint",
        execution=_execution(checkpoint={"query": "failed with checkpoint", "scratch": {}}),
    )
    await repo.set_status(run_id, "error")

    outcomes = await asyncio.gather(
        repo.requeue_failed_run(run_id),
        repo.requeue_failed_run(run_id),
    )

    assert sorted(outcomes) == [False, True]
    assert await repo.get_run_status(run_id) == "running"
    claimed = await repo.claim_next_run("worker-1")
    assert claimed is not None
    assert claimed.run_id == run_id
    assert claimed.resumed is True


@pytest.mark.asyncio
async def test_concurrent_workers_never_double_claim():
    """并发领取的核心断言：N 个 worker 抢 M 个任务，每个任务恰好派发一次。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    await create_all(engine)
    repo = SqlRepository(make_sessionmaker(engine))
    try:
        expected = set()
        for i in range(12):
            expected.add(await _enqueue(repo, f"q{i}"))

        async def drain(worker: str) -> list[str]:
            claimed: list[str] = []
            while True:
                result = await repo.claim_next_run(worker)
                if result is None:
                    return claimed
                claimed.append(result.run_id)
                await asyncio.sleep(0)

        batches = await asyncio.gather(*(drain(f"worker-{i}") for i in range(4)))
        claimed = [run_id for batch in batches for run_id in batch]

        assert len(claimed) == len(set(claimed)), "同一个 run 被派发给了多个 worker"
        assert set(claimed) == expected
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_claim_ignores_runs_without_a_workflow_row(repo):
    """没有 workflow 行就没有可执行的定义，也没有可 fence 的租约——不领取。"""
    run_id, _ = await repo.create_run_once("no execution", request_hash="", claimable=True)
    assert run_id
    assert await repo.claim_next_run("worker-1") is None


@pytest.mark.asyncio
async def test_claim_candidate_query_uses_the_queue_index():
    """索引缺失时队列轮询会随历史增长退化为全表扫描。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    await create_all(engine)
    try:
        async with engine.connect() as connection:
            indexes = await connection.run_sync(
                lambda sync_conn: {
                    index["name"] for index in sa.inspect(sync_conn).get_indexes("research_run")
                }
            )
        assert "ix_research_run_claimable" in indexes
    finally:
        await engine.dispose()
