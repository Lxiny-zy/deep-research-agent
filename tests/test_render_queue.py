"""Durable render ownership and capacity must be shared across queue instances."""

import asyncio

import pytest

from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.sql_repository import SqlRepository


@pytest.fixture(params=["memory", "sql"])
async def queues(request, tmp_path):
    from deep_research.render_queue import MemoryRenderQueue, SqlRenderQueue

    engine = None
    if request.param == "sql":
        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'render.db'}")
        await create_all(engine)
        sessions = make_sessionmaker(engine)
        run_id = await SqlRepository(sessions).create_run("render")
        first, second = SqlRenderQueue(sessions), SqlRenderQueue(sessions)
    else:
        first = second = MemoryRenderQueue()
        run_id = "run"
    yield first, second, run_id
    if engine:
        await engine.dispose()


async def reserve(queue, run_id, key, now=100):
    return await queue.reserve(
        key=key, pool="shared", run_id=run_id, kind="bundle", payload={"source": key}, now=now
    )


async def test_queue_reservation_is_durable_and_rejects_reused_identity(queues):
    from deep_research.render_queue import RenderConflict

    first, second, run_id = queues
    jobs = await asyncio.gather(
        *[reserve(first if i % 2 else second, run_id, "same") for i in range(8)]
    )
    assert len({job.id for job in jobs}) == 1
    with pytest.raises(RenderConflict):
        await second.reserve(
            key="same",
            pool="shared",
            run_id=run_id,
            kind="bundle",
            payload={"changed": True},
            now=100,
        )
    assert (await second.get(jobs[0].id)).payload == {"source": "same"}


async def test_capacity_is_shared_and_jobs_are_claimed_in_order(queues):
    first, second, run_id = queues
    jobs = [await reserve(first, run_id, f"job-{i}", now=100 + i) for i in range(4)]
    claims = await asyncio.gather(
        *[
            (first if i % 2 else second).claim(
                "shared", f"owner-{i}", now=110, limit=2, lease_seconds=30
            )
            for i in range(8)
        ]
    )
    active = [job for job in claims if job is not None]
    assert {job.id for job in active} == {job.id for job in jobs[:2]}
    assert await second.claim("shared", "late", now=120, limit=2, lease_seconds=30) is None


async def test_expired_owner_cannot_publish_and_a_successor_resumes_the_same_job(queues):
    first, second, run_id = queues
    queued = await reserve(first, run_id, "recover")
    original = await first.claim("shared", "old", now=100, limit=2, lease_seconds=10)
    assert original.id == queued.id
    assert not await first.finish(queued.id, "old", {"version": "stale"}, now=111)
    resumed = await second.claim("shared", "new", now=111, limit=2, lease_seconds=10)
    assert resumed.id == queued.id and resumed.attempts == 2
    assert not await first.finish(queued.id, "old", {"version": "stale"}, now=112)
    assert await second.finish(queued.id, "new", {"version": "saved"}, now=112)
    assert (await first.get(queued.id)).result == {"version": "saved"}


async def test_three_failures_without_progress_stop_background_retries(queues):
    first, second, run_id = queues
    job = await reserve(first, run_id, "failure")
    for index in range(3):
        claimed = await first.claim("shared", "owner", now=100 + index, limit=2, lease_seconds=10)
        assert claimed.id == job.id
        assert await first.progress(job.id, "owner", "unchanged", now=100 + index)
        assert await first.fail(
            job.id,
            "owner",
            {"kind": "io", "message": "temporary"},
            retryable=True,
            progress="unchanged",
            now=100 + index,
        )
    assert (await second.get(job.id)).status == "error"
    assert await second.claim("shared", "again", now=120, limit=2, lease_seconds=10) is None


async def test_new_durable_progress_resets_the_stall_budget(queues):
    first, second, run_id = queues
    job = await reserve(first, run_id, "progress")
    for index in range(4):
        claimed = await first.claim("shared", "owner", now=100 + index, limit=2, lease_seconds=10)
        assert claimed.id == job.id
        token = "initial" if index < 2 else "completed-format"
        assert await first.progress(job.id, "owner", token, now=100 + index)
        await first.fail(
            job.id, "owner", {"kind": "io"}, retryable=True, progress=token, now=100 + index
        )
    assert (await second.get(job.id)).status == "pending"


async def test_removed_jobs_cannot_be_completed_by_their_old_owner(queues):
    first, second, run_id = queues
    job = await reserve(first, run_id, "removed")
    await first.claim("shared", "owner", now=100, lease_seconds=10)
    await second.remove_run(run_id)
    assert await first.get(job.id) is None
    assert not await first.finish(job.id, "owner", {"saved": True}, now=101)


async def test_only_a_new_explicit_request_can_restart_a_terminal_failure(queues):
    first, second, run_id = queues
    arguments = dict(
        key="explicit",
        pool="shared",
        run_id=run_id,
        kind="export",
        payload={"q": "same"},
        restart_failed=True,
    )
    job = await first.reserve(**arguments, request_token="click-1", now=100)
    await second.reserve(**arguments, request_token="subscriber-1", now=100)
    await first.claim("shared", "owner", now=100, lease_seconds=30)
    await first.fail(job.id, "owner", {"kind": "invalid"}, now=101)
    for token in (None, "click-1", "subscriber-1"):
        replay = await second.reserve(**arguments, request_token=token, now=102)
        assert replay.status == "error"
    retried = await second.reserve(**arguments, request_token="click-2", now=103)
    assert retried.status == "pending" and retried.id == job.id
