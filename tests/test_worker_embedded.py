"""Embedded and standalone workers share admission, cancellation and shutdown."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from deep_research.config import Settings
from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.observability import EventHub
from deep_research.orchestration import OrchestrationRuntime
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.worker import Worker
from tests.test_worker import _RecordingExecutor


async def enqueue(repo, query):
    execution = OrchestrationRuntime().start("quick", {"query": query})
    run_id, _ = await repo.create_run_once(
        query, request_hash="", execution=execution, claimable=True
    )
    return run_id


async def stop(worker, loop):
    worker.request_stop()
    await asyncio.wait_for(loop, timeout=2)


async def test_local_wake_dispatches_through_injected_execution_and_callbacks():
    polled = asyncio.Event()

    class Repository(InMemoryRepository):
        async def claim_next_run(self, *args, **kwargs):
            claimed = await super().claim_next_run(*args, **kwargs)
            polled.set()
            return claimed

    repo = Repository()
    live = {}
    executor = _RecordingExecutor(ExecutionContext(repo=repo, live=live))
    called = []
    callbacks = []
    finished = asyncio.Event()

    async def execute(*args, **kwargs):
        called.append(args[0])
        assert executor.ctx.live is live
        await executor.execute(*args, **kwargs)

    def started(claimed, task):
        callbacks.append(("started", claimed.run_id, task))

    def completed(claimed, task):
        callbacks.append(("finished", claimed.run_id, task))
        finished.set()

    worker = Worker(
        repo,
        executor,
        Settings(worker_poll_seconds=30),
        execute=execute,
        on_task_started=started,
        on_task_finished=completed,
    )
    loop = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(polled.wait(), timeout=1)
        run_id = await enqueue(repo, "Q")
        worker.wake()
        await asyncio.wait_for(finished.wait(), timeout=1)
        assert called == [run_id]
        assert [entry[:2] for entry in callbacks] == [("started", run_id), ("finished", run_id)]
        assert callbacks[0][2] is callbacks[1][2]
        assert await repo.get_run_status(run_id) == "done"
    finally:
        await stop(worker, loop)


async def test_dispatch_observability_does_not_mutate_checkpoint_content():
    metadata = {"kind": "light", "priority": 2, "queue_seconds": 12.5}

    class Repository(InMemoryRepository):
        async def claim_next_run(self, *args, **kwargs):
            claimed = await super().claim_next_run(*args, **kwargs)
            return (
                SimpleNamespace(**{**vars(claimed), "dispatch": metadata})
                if claimed is not None
                else None
            )

    repo = Repository()
    run_id = await enqueue(repo, "Q")
    executor = _RecordingExecutor(ExecutionContext(repo=repo))
    worker = Worker(repo, executor, Settings())
    await worker._tick()
    await asyncio.gather(*list(worker._running))
    events = await repo.get_events(run_id)
    assert any(
        event.data
        and event.data.get("category") == "schedule_dispatch"
        and all(event.data.get(key) == value for key, value in metadata.items())
        for event in events
    )
    assert (await repo.get_run(run_id)).orchestration.checkpoint == {}


async def test_completion_wakes_waiting_queue_without_waiting_for_poll_interval():
    repo = InMemoryRepository()
    release = asyncio.Event()
    executor = _RecordingExecutor(ExecutionContext(repo=repo), block=release)
    first = await enqueue(repo, "first")
    worker = Worker(repo, executor, Settings(max_active_runs=1, worker_poll_seconds=30))
    loop = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(executor.started.wait(), timeout=1)
        second = await enqueue(repo, "second")
        release.set()
        await asyncio.wait_for(executor.wait_for_calls(2), timeout=1)
        assert [call["run_id"] for call in executor.calls] == [first, second]
    finally:
        release.set()
        await stop(worker, loop)


async def test_queued_cancellations_are_bounded_and_close_matching_local_hubs():
    repo = InMemoryRepository()
    live = {}
    executor = _RecordingExecutor(ExecutionContext(repo=repo, live=live))
    run_ids = [await enqueue(repo, str(index)) for index in range(35)]
    for run_id in run_ids:
        await repo.request_cancel(run_id)
        live[run_id] = EventHub()
    worker = Worker(repo, executor, Settings())
    assert await worker.settle_cancellations() == 32
    assert len(await repo.list_runs(status="cancelling", limit=50)) == 3
    assert await worker.settle_cancellations() == 3
    assert not live
    assert not executor.calls
    assert not worker._running
    events = await asyncio.gather(*(repo.get_events(run_id) for run_id in run_ids))
    assert all(event.type == "cancelled" for run_events in events for event in run_events)


async def test_cancellation_scan_rotates_past_busy_owners_without_stealing_their_leases():
    repo = InMemoryRepository()
    # list_runs sorts newest first, so create the unleased target first.
    target = await enqueue(repo, "queued")
    await repo.request_cancel(target)
    active = []
    for index in range(32):
        run_id = await enqueue(repo, f"active-{index}")
        assert await repo.acquire_lease(run_id, f"foreign-{index}")
        await repo.request_cancel(run_id)
        active.append(run_id)
    worker = Worker(repo, _RecordingExecutor(ExecutionContext(repo=repo)), Settings())
    assert await worker.settle_cancellations() == 0
    assert await worker.settle_cancellations() == 1
    assert await repo.get_run_status(target) == "cancelled"
    for run_id in active:
        assert await repo.get_run_status(run_id) == "cancelling"
        assert not await repo.acquire_lease(run_id, "unrelated-owner")


async def test_cancellation_between_claim_and_execution_does_not_start_provider_work():
    repo = InMemoryRepository()
    run_id = await enqueue(repo, "Q")
    executor = _RecordingExecutor(ExecutionContext(repo=repo))
    started = []
    worker = Worker(repo, executor, Settings(), on_task_started=lambda *args: started.append(args))
    await worker._tick()
    await repo.request_cancel(run_id)
    await asyncio.gather(*list(worker._running))
    assert not executor.calls
    assert not started
    assert await repo.get_run_status(run_id) == "cancelled"
    assert await repo.acquire_lease(run_id, "check-released")


async def test_full_execution_capacity_does_not_block_queued_cancellation():
    changed = asyncio.Event()

    class Repository(InMemoryRepository):
        async def set_status(self, run_id, status, **kwargs):
            await super().set_status(run_id, status, **kwargs)
            if status == "cancelled":
                changed.set()

    repo = Repository()
    release = asyncio.Event()
    executor = _RecordingExecutor(ExecutionContext(repo=repo), block=release)
    await enqueue(repo, "active")
    worker = Worker(repo, executor, Settings(max_active_runs=1, worker_poll_seconds=30))
    loop = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(executor.started.wait(), timeout=1)
        target = await enqueue(repo, "queued")
        await repo.request_cancel(target)
        worker.wake()
        await asyncio.wait_for(changed.wait(), timeout=1)
        assert len(executor.calls) == 1
        assert await repo.get_run_status(target) == "cancelled"
    finally:
        release.set()
        await stop(worker, loop)


async def test_callback_failure_does_not_abandon_a_claimed_run_or_prevent_shutdown():
    repo = InMemoryRepository()
    run_id = await enqueue(repo, "Q")
    executor = _RecordingExecutor(ExecutionContext(repo=repo))
    finished = asyncio.Event()

    def broken(*_):
        raise RuntimeError("bookkeeping failure")

    def finished_broken(*_):
        finished.set()
        broken()

    worker = Worker(
        repo,
        executor,
        Settings(),
        on_task_started=broken,
        on_task_finished=finished_broken,
    )
    loop = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(finished.wait(), timeout=1)
        assert await repo.get_run_status(run_id) == "done"
        assert not worker._running
    finally:
        await stop(worker, loop)


async def test_embedded_uncooperative_execute_has_bounded_stop_without_process_exit(monkeypatch):
    repo = InMemoryRepository()
    await enqueue(repo, "Q")
    executor = RunExecutor(ExecutionContext(repo=repo))
    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(*_args, **_kwargs):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()

    monkeypatch.setattr("deep_research.worker._CLEANUP_SECONDS", 0.02)
    worker = Worker(repo, executor, Settings(worker_shutdown_grace_seconds=0), execute=execute)
    loop = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        await stop(worker, loop)
        assert worker.requires_hard_exit
    finally:
        release.set()
        await asyncio.gather(*list(worker._running), return_exceptions=True)
        if not loop.done():
            await stop(worker, loop)
