from __future__ import annotations

import asyncio

import pytest

from deep_research.config import Settings
from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.orchestrator import create_initial_execution
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.persistence.repository import LeaseLostError
from deep_research.worker import Worker


@pytest.mark.parametrize("same_process", [True, False])
async def test_each_claim_fences_previous_owner_even_when_worker_name_is_reused(same_process):
    repo = InMemoryRepository()
    settings = Settings(max_active_runs=2)
    run_id, _ = await repo.create_run_once(
        "Q",
        request_hash="",
        execution=create_initial_execution("Q", "quick", settings),
        claimable=True,
    )
    release = asyncio.Event()
    owners = []

    async def execute(run_id, *args, **kwargs):
        owner = kwargs["lease_owner"]
        owners.append(owner)
        try:
            await release.wait()
        finally:
            await repo.release_lease(run_id, owner)

    first = Worker(
        repo, RunExecutor(ExecutionContext(repo=repo)), settings, name="same-label", execute=execute
    )
    second = (
        first
        if same_process
        else Worker(repo, first.executor, settings, name="same-label", execute=execute)
    )
    try:
        await first._tick()
        await asyncio.sleep(0)
        old_owner = repo._runs[run_id].lease_owner
        assert await repo.renew_lease(run_id, old_owner, seconds=0)
        await second._tick()
        await asyncio.sleep(0)
        new_owner = repo._runs[run_id].lease_owner
        assert old_owner != new_owner and len(set(owners)) == 2
        with pytest.raises(LeaseLostError):
            await repo.set_status(run_id, "done", lease_owner=old_owner)
        await repo.release_lease(run_id, old_owner)
        assert repo._runs[run_id].lease_owner == new_owner
    finally:
        release.set()
        for worker in {first, second}:
            await worker._drain()
            await repo.remove_worker(worker.name)
