from __future__ import annotations

import asyncio
from collections import Counter

import httpx
import pytest

from deep_research import api
from deep_research.access import ApiCredential, Principal
from deep_research.config import Settings
from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.sql_repository import SqlRepository
from deep_research.worker import Worker


@pytest.fixture(params=["inline", "worker"])
async def service(request, monkeypatch, tmp_path):
    settings = Settings(
        execution_mode=request.param,
        max_active_runs=3,
        max_queued_runs=5,
        worker_poll_seconds=0.01,
        worker_shutdown_grace_seconds=1,
        intent_enabled=False,
        orchestration_mode="legacy",
        artifact_root=str(tmp_path / "artifacts"),
        api_key="",
        api_credentials=(
            ApiCredential(Principal("alice", "researcher"), "alice-test-secret"),
            ApiCredential(Principal("bob", "researcher"), "bob-test-secret"),
        ),
    )
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'queue.db').as_posix()}")
    await create_all(engine)
    repo = SqlRepository(make_sessionmaker(engine))
    for name, value in {
        "settings": settings,
        "repo": repo,
        "catalog": None,
        "executor": None,
        "live": {},
        "tasks": set(),
        "run_tasks": {},
        "cancellation_requested": set(),
        "inline_worker": None,
        "inline_worker_task": None,
    }.items():
        monkeypatch.setattr(api.app.state, name, value, raising=False)
    monkeypatch.setattr(api, "_run_limiter", api._RateLimiter(1000, 60))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            yield client, repo, settings
    finally:
        await engine.dispose()


def auth(who):
    return {"Authorization": f"Bearer {who}-test-secret"}


async def submit(client, who, query, workflow="research_quick", **extra):
    response = await client.post(
        "/api/runs", headers=auth(who), json={"query": query, "workflow": workflow, **extra}
    )
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


async def wait_for(check, *, seconds=5):
    async with asyncio.timeout(seconds):
        while not await check():
            await asyncio.sleep(0.01)


async def test_both_topologies_queue_without_lease_and_reserve_light_execution(
    service, monkeypatch
):
    client, repo, settings = service
    ids = {
        "a-heavy": await submit(client, "alice", "a-heavy", tier="light"),
        "a-next": await submit(client, "alice", "a-next", tier="light"),
        "b-heavy": await submit(client, "bob", "b-heavy", tier="light"),
        "a-light": await submit(client, "alice", "a-light", workflow="quick"),
    }
    assert api.app.state.live == {} and api.app.state.run_tasks == {}
    for run_id in ids.values():
        assert await repo.get_run_status(run_id) == "pending"
        assert await repo.acquire_lease(run_id, "probe")
        await repo.release_lease(run_id, "probe")
    released = {query: asyncio.Event() for query in ids}
    started = []
    owners = set()

    async def body(run_id, query, frozen, **kwargs):
        owner = kwargs["lease_owner"]
        owners.add(owner)
        started.append(query)
        try:
            await released[query].wait()
            await repo.finalize(run_id, elapsed=1, total_tokens=0, lease_owner=owner)
        finally:
            await repo.release_lease(run_id, owner)

    async def embedded(app, *args, **kwargs):
        await body(*args, **kwargs)

    monkeypatch.setattr(api, "_execute", embedded)
    consumer = (
        api._make_inline_worker(api.app, settings)
        if settings.execution_mode == "inline"
        else Worker(repo, RunExecutor(ExecutionContext(repo=repo)), settings, execute=body)
    )
    task = asyncio.create_task(consumer.run_forever())
    try:

        async def first_three():
            return len(started) == 3

        await wait_for(first_three)
        assert set(started) == {"a-heavy", "b-heavy", "a-light"}
        assert await repo.get_run_status(ids["a-next"]) == "pending"
        assert len(consumer._running) == 3
        assert await repo.claim_next_run("competitor", max_active_runs=3) is None
        # Same user's light work used the reservation despite its heavy backlog.
        released["a-heavy"].set()

        async def next_started():
            return "a-next" in started

        await wait_for(next_started)
        for event in released.values():
            event.set()

        async def all_finished():
            return all([await repo.get_run_status(run_id) == "done" for run_id in ids.values()])

        await wait_for(all_finished)
        assert Counter(started) == Counter(ids.keys())
        assert len(owners) == len(ids)  # Every claim has a fresh fencing token.
        for run_id in ids.values():
            events = await repo.get_events(run_id)
            dispatch = [
                event.data
                for event in events
                if event.data and event.data.get("category") == "schedule_dispatch"
            ]
            assert len(dispatch) == 1 and dispatch[0]["queue_seconds"] >= 0
    finally:
        for event in released.values():
            event.set()
        consumer.request_stop(reason="test_complete")
        await asyncio.wait_for(task, 10)


async def test_pending_cancel_and_resume_cannot_bypass_queue(service, monkeypatch):
    client, repo, settings = service
    run_id = await submit(client, "alice", "cancel before dispatch", workflow="quick")
    resumed = await client.post(f"/api/runs/{run_id}/resume", headers=auth("alice"))
    assert resumed.status_code == 409
    cancelled = await client.post(f"/api/runs/{run_id}/cancel", headers=auth("alice"))
    assert cancelled.status_code == 202
    calls = []

    async def body(*args, **kwargs):
        calls.append(args)

    consumer = Worker(repo, RunExecutor(ExecutionContext(repo=repo)), settings, execute=body)
    await consumer._tick()
    await consumer._drain()
    await repo.remove_worker(consumer.name)
    assert await repo.get_run_status(run_id) == "cancelled" and not calls
    terminal = await client.post(f"/api/runs/{run_id}/resume", headers=auth("alice"))
    assert terminal.status_code == 409


async def test_shared_capacity_and_idempotent_replay_do_not_depend_on_local_admission(service):
    client, repo, settings = service
    settings.max_active_runs = 1
    settings.max_queued_runs = 0
    api.app.state.run_admission = api.RunAdmission(9, 9)
    headers = {**auth("alice"), "Idempotency-Key": "same-queued-input"}
    body = {"query": "first", "workflow": "quick"}
    first = await client.post("/api/runs", headers=headers, json=body)
    repeated = await client.post("/api/runs", headers=headers, json=body)
    rejected = await client.post(
        "/api/runs", headers=auth("bob"), json={"query": "second", "workflow": "quick"}
    )
    assert first.status_code == repeated.status_code == 202
    assert first.json() == repeated.json()
    assert rejected.status_code == 503
    assert api.app.state.run_admission.active == api.app.state.run_admission.queued == 0
