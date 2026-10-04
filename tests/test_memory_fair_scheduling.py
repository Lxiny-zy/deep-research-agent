"""In-memory queue integration for the shared durable fairness policy."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from deep_research.checkpoints import RUN_SETTINGS_KEY
from deep_research.orchestration import OrchestrationRuntime
from deep_research.persistence import memory_repository
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.scheduling import identity_key
from deep_research.workflows import get_workflow


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        value = datetime(2026, 1, 1, tzinfo=UTC)

        def advance(self, seconds):
            self.value += timedelta(seconds=seconds)

    clock = Clock()

    class ControlledDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.value.astimezone(tz) if tz else clock.value.replace(tzinfo=None)

    monkeypatch.setattr(memory_repository, "datetime", ControlledDatetime)
    return clock


def execution(kind="light"):
    runtime = OrchestrationRuntime()
    workflow = "quick" if kind == "light" else "deep"
    result = runtime.start(workflow, {"query": "Q"})
    runtime.save_checkpoint(
        {
            "query": "Q",
            "scratch": {
                RUN_SETTINGS_KEY: {
                    "research_tier": "light" if kind == "light" else "deep",
                    "max_concurrency": 1,
                }
            },
        },
        get_workflow(workflow).model_dump(mode="json"),
    )
    return result


async def enqueue(repo, identity="a", *, kind="light", priority=1, project=None):
    run_id, _ = await repo.create_run_once(
        "Q",
        request_hash="",
        execution=execution(kind),
        claimable=True,
        owner_id=identity,
        project_id=project,
        schedule_priority=priority,
    )
    return run_id


async def finish(repo, claimed):
    assert claimed is not None
    await repo.set_status(claimed.run_id, "done", lease_owner=claimed.lease_owner)
    await repo.release_lease(claimed.run_id, claimed.lease_owner)


@pytest.mark.parametrize("maximum", [None, 2])
async def test_identities_alternate_without_project_ids_creating_extra_shares(clock, maximum):
    repo = InMemoryRepository()
    a1 = await enqueue(repo, project="first")
    clock.advance(1)
    a2 = await enqueue(repo, project="second")
    clock.advance(1)
    b1 = await enqueue(repo, "b")
    claimed = [await repo.claim_next_run("worker-1", max_active_runs=maximum)]
    claimed.append(await repo.claim_next_run("worker-2", max_active_runs=maximum))
    assert [item.run_id for item in claimed] == [a1, b1]
    await finish(repo, claimed[0])
    assert (await repo.claim_next_run("worker-3", max_active_runs=maximum)).run_id == a2


async def test_deficit_credit_charges_task_cost_instead_of_request_count(clock):
    repo = InMemoryRepository()
    heavy = await enqueue(repo, "a", kind="heavy")
    light = []
    for _ in range(4):
        clock.advance(1)
        light.append(await enqueue(repo, "b"))
    order = []
    for index in range(5):
        claimed = await repo.claim_next_run(f"worker-{index}")
        order.append(claimed.run_id)
        await finish(repo, claimed)
    assert order == [*light[:3], heavy, light[3]]
    assert repo._scheduler_credits[identity_key("a")].deficit == 0
    assert repo._scheduler_credits[identity_key("b")].deficit == 0


async def test_heavy_reservation_does_not_block_light_work_from_the_same_identity(clock):
    repo = InMemoryRepository()
    first = await enqueue(repo, kind="heavy")
    clock.advance(1)
    second = await enqueue(repo, kind="heavy", priority=2)
    # Claim the higher-priority heavy task, then its other heavy task is blocked.
    running = await repo.claim_next_run("heavy-worker", max_active_runs=2)
    assert running.run_id == second
    clock.advance(1)
    light = await enqueue(repo, priority=0)
    assert (await repo.claim_next_run("light-worker", max_active_runs=2)).run_id == light
    assert await repo.claim_next_run("third-worker", max_active_runs=2) is None
    await finish(repo, running)
    assert (await repo.claim_next_run("third-worker", max_active_runs=2)).run_id == first


async def test_heavy_global_and_identity_caps_leave_one_slot_for_light_work(clock):
    repo = InMemoryRepository()
    a = [await enqueue(repo, "a", kind="heavy") for _ in range(3)]
    first = await repo.claim_next_run("worker-1", max_active_runs=4)
    second = await repo.claim_next_run("worker-2", max_active_runs=4)
    assert {first.run_id, second.run_id}.issubset(a)
    assert await repo.claim_next_run("worker-3", max_active_runs=4) is None
    b = await enqueue(repo, "b", kind="heavy")
    assert (await repo.claim_next_run("worker-3", max_active_runs=4)).run_id == b
    await enqueue(repo, "c", kind="heavy")
    assert await repo.claim_next_run("worker-4", max_active_runs=4) is None
    light = await enqueue(repo, "a")
    assert (await repo.claim_next_run("worker-4", max_active_runs=4)).run_id == light


@pytest.mark.parametrize("age", [0, 90])
async def test_priority_and_wait_aging_order_work_within_the_identity(clock, age):
    repo = InMemoryRepository()
    old = await enqueue(repo, priority=0)
    clock.advance(age)
    urgent = await enqueue(repo, priority=2)
    claimed = await repo.claim_next_run("worker")
    assert claimed.run_id == (old if age else urgent)


async def test_requeue_and_checkpoint_changes_preserve_frozen_cost_and_identity_credit(clock):
    repo = InMemoryRepository()
    await enqueue(repo, "a", kind="heavy")
    retried = await enqueue(repo, "b")
    claimed = await repo.claim_next_run("worker")
    assert claimed.run_id == retried
    credits, state = dict(repo._scheduler_credits), repo._scheduler_state
    await repo.set_status(retried, "error", lease_owner="worker")
    await repo.release_lease(retried, "worker")
    await repo.save_orchestration(retried, execution("heavy"))
    assert await repo.requeue_failed_run(retried)
    assert repo._scheduler_credits == credits
    assert repo._scheduler_state == state
    record = repo._runs[retried]
    assert (record.schedule_cost, record.schedule_class, record.schedule_priority) == (
        1,
        "light",
        1,
    )
    resumed = await repo.claim_next_run("replacement")
    assert resumed.run_id == retried and resumed.resumed
    assert resumed.attempt > claimed.attempt


@pytest.mark.parametrize("release_before_claim", [False, True])
async def test_lease_execution_and_deferred_cooldown_do_not_accrue_queue_aging(
    clock, release_before_claim
):
    repo = InMemoryRepository()
    old = await enqueue(repo, priority=0)
    claimed = await repo.claim_next_run("original", lease_seconds=120)
    assert claimed.run_id == old
    if release_before_claim:
        assert await repo.defer_run(
            old, claimed.execution, lease_owner="original", not_before=clock.value.timestamp() + 120
        )
        await repo.release_lease(old, "original")
    clock.advance(121)
    urgent = await enqueue(repo, priority=2)
    assert (await repo.claim_next_run("new-worker")).run_id == urgent


async def test_concurrent_claims_respect_global_capacity_and_cancelling_leases(clock):
    repo = InMemoryRepository()
    for identity in ("a", "b", "c"):
        await enqueue(repo, identity)
    claims = await asyncio.gather(
        *(repo.claim_next_run(f"worker-{i}", max_active_runs=2) for i in range(3))
    )
    accepted = [claim for claim in claims if claim is not None]
    assert len(accepted) == len({claim.run_id for claim in accepted}) == 2
    await repo.set_status(accepted[0].run_id, "cancelling", lease_owner=accepted[0].lease_owner)
    credits, state = dict(repo._scheduler_credits), repo._scheduler_state
    assert await repo.claim_next_run("later-worker", max_active_runs=2) is None
    assert repo._scheduler_credits == credits and repo._scheduler_state == state


@pytest.mark.parametrize("priority", [True, -1, 3, 1.5])
async def test_invalid_priority_cannot_create_partial_queue_records(clock, priority):
    repo = InMemoryRepository()
    with pytest.raises(ValueError, match="schedule_priority"):
        await enqueue(repo, priority=priority)
    assert not repo._runs and not repo._scheduler_credits


async def test_legacy_enqueue_requires_checkpoint_and_never_takes_a_live_lease(clock):
    repo = InMemoryRepository()
    missing = await repo.create_run("no execution")
    assert not await repo.enqueue_run(missing)
    empty = await repo.create_run("empty", execution=OrchestrationRuntime().start("quick", {}))
    assert not await repo.enqueue_run(empty)
    run_id = await repo.create_run("recoverable", execution=execution())
    assert await repo.acquire_lease(run_id, "owner", seconds=120)
    assert not await repo.enqueue_run(run_id)
    assert repo._runs[run_id].lease_owner == "owner"
    await repo.release_lease(run_id, "owner")
    await repo.set_status(run_id, "cancelling")
    assert not await repo.enqueue_run(run_id)
    await repo.set_status(run_id, "running")
    assert await repo.enqueue_run(run_id)
    assert not await repo.enqueue_run(run_id)
    assert (await repo.claim_next_run("replacement")).resumed


async def test_idempotent_replay_keeps_the_original_frozen_scheduling_inputs(clock):
    repo = InMemoryRepository()
    original = execution()
    run_id, created = await repo.create_run_once(
        "Q",
        request_hash="hash",
        idempotency_key="request",
        execution=original,
        claimable=True,
        owner_id="a",
        schedule_priority=2,
    )
    assert created
    original.checkpoint["scratch"][RUN_SETTINGS_KEY]["research_tier"] = "deep"
    replay_id, replay_created = await repo.create_run_once(
        "Q",
        request_hash="hash",
        idempotency_key="request",
        execution=execution("heavy"),
        claimable=True,
        owner_id="a",
        schedule_priority=0,
        max_inflight=1,
    )
    assert replay_id == run_id and not replay_created
    record = repo._runs[run_id]
    assert (record.schedule_cost, record.schedule_class, record.schedule_priority) == (
        1,
        "light",
        2,
    )
    assert record.orchestration.checkpoint["scratch"][RUN_SETTINGS_KEY]["research_tier"] == "light"
    assert len(repo._runs) == 1


async def test_dispatch_reports_actual_queue_wait_without_changing_checkpoint(clock):
    repo = InMemoryRepository()
    run_id = await enqueue(repo, priority=2)
    ready_at = clock.value.timestamp()
    checkpoint = repo._runs[run_id].orchestration.model_copy(deep=True).checkpoint
    clock.advance(45)
    claimed = await repo.claim_next_run("worker")
    assert claimed.dispatch == {
        "kind": "light",
        "cost": 1,
        "priority": 2,
        "ready_at": ready_at,
        "queue_seconds": 45.0,
    }
    assert claimed.execution.checkpoint == checkpoint
    assert repo._runs[run_id].orchestration.checkpoint == checkpoint
