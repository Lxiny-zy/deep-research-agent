"""HTTP requests use real embedded consumers and the shared repository queue."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI

from deep_research import api
from deep_research.execution import RunExecutor
from deep_research.orchestrator import DeepResearchAgent, create_initial_execution
from tests.fakes import FakeLLM, FakeSearch
from tests.queue_helpers import drain_inline
from tests.test_api import _client, _finish_stub, _queue_app, _running_consumer
from tests.test_api import repo as repo


async def test_http_cancel_queued_run_settles_without_claiming_or_running_a_model(
    repo, monkeypatch
):
    execute = AsyncMock(side_effect=AssertionError("cancelled queue entry must never execute"))
    monkeypatch.setattr(api, "_execute", execute)
    async with _client() as client:
        created = await client.post("/api/runs", json={"query": "Q", "workflow": "quick"})
        run_id = created.json()["run_id"]
        first = await client.post(f"/api/runs/{run_id}/cancel")
        repeated = await client.post(f"/api/runs/{run_id}/cancel")
        assert first.status_code == repeated.status_code == 202
        assert first.json()["status"] == repeated.json()["status"] == "cancelling"
        assert repo._runs[run_id].lease_owner is None
        assert not api.app.state.live and not api.app.state.run_tasks
        await drain_inline(api.app)
        final = await client.get(f"/api/runs/{run_id}")
        again = await client.post(f"/api/runs/{run_id}/cancel")
    assert final.json()["status"] == "cancelled"
    assert again.json()["status"] == "cancelled"
    assert any(event.type == "cancelled" for event in await repo.get_events(run_id))
    execute.assert_not_awaited()


async def test_http_active_cancellation_runs_real_executor_cleanup(repo, monkeypatch):
    started = asyncio.Event()

    class Agent(DeepResearchAgent):
        async def _run_workflow(self, query, run_id):
            started.set()
            await asyncio.Event().wait()

    async def build_agent(self, settings, **kwargs):
        search = FakeSearch()
        return Agent(settings, llm=FakeLLM(), search_tool=search, **kwargs), search

    async def execute(app, run_id, query, settings, **kwargs):
        await api._executor(app).execute(run_id, query, settings, **kwargs)

    monkeypatch.setattr(RunExecutor, "build_agent", build_agent)
    monkeypatch.setattr(api, "_execute", execute)
    async with _running_consumer(api.app), _client() as client:
        created = await client.post("/api/runs", json={"query": "Q", "workflow": "quick"})
        run_id = created.json()["run_id"]
        await asyncio.wait_for(started.wait(), 2)
        task = api.app.state.run_tasks[run_id]
        assert not await repo.acquire_lease(run_id, "competitor")
        cancelled = await client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 202
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)
        assert await repo.get_run_status(run_id) == "cancelled"
        assert run_id not in api.app.state.live
        assert run_id not in api.app.state.run_tasks
        assert await repo.acquire_lease(run_id, "after-cleanup")


async def test_two_http_instances_share_admission_and_idempotent_execution(repo, monkeypatch):
    settings = replace(api.app.state.settings, max_active_runs=1, max_queued_runs=0)
    api.app.state.settings = settings
    replica = FastAPI()
    replica.include_router(api.app.router)
    replica.state = _queue_app(repo, settings).state
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def execute(app, run_id, *args, **kwargs):
        calls.append((run_id, kwargs["lease_owner"]))
        started.set()
        try:
            await release.wait()
        finally:
            await _finish_stub(app, run_id, kwargs["lease_owner"])

    monkeypatch.setattr(api, "_execute", execute)
    monkeypatch.setattr(api, "_check_rate_limit", AsyncMock(return_value=None))
    headers = {"Idempotency-Key": "same-across-replicas"}
    body = {"query": "Q", "workflow": "quick"}
    async with (
        _client() as first_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=replica), base_url="http://test"
        ) as second,
    ):
        created = await first_client.post("/api/runs", json=body, headers=headers)
        replay = await second.post("/api/runs", json=body, headers=headers)
        rejected = await second.post("/api/runs", json={**body, "query": "over capacity"})
        assert created.status_code == replay.status_code == 202
        assert created.json() == replay.json()
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert rejected.status_code == 503
        assert len(await repo.list_runs()) == 1
        assert calls == []
        async with _running_consumer(api.app), _running_consumer(replica):
            try:
                await asyncio.wait_for(started.wait(), 2)
                assert len(calls) == 1
                assert await repo.claim_next_run("third-consumer", max_active_runs=1) is None
                assert (
                    await second.post("/api/runs", json={**body, "query": "still full"})
                ).status_code == 503
            finally:
                release.set()
    assert len(calls) == 1
    assert await repo.get_run_status(created.json()["run_id"]) == "done"


async def test_http_resume_waits_for_old_lease_and_preserves_frozen_inputs(repo, monkeypatch):
    original = replace(api.app.state.settings, max_rounds=1, max_concurrency=2)
    execution = create_initial_execution("original input", "quick", original)
    execution.checkpoint["scratch"]["verified_material"] = "retained evidence"
    run_id = await repo.create_run("original input", execution=execution, lease_owner="old-owner")
    await repo.set_status(run_id, "running", lease_owner="old-owner")
    api.app.state.settings = replace(original, max_rounds=5, max_concurrency=9)
    calls = []

    async def execute(app, run_id, query, settings, **kwargs):
        calls.append((run_id, query, settings, kwargs["resume_execution"]))
        await _finish_stub(app, run_id, kwargs["lease_owner"])

    monkeypatch.setattr(api, "_execute", execute)
    async with _client() as client:
        response = await client.post(f"/api/runs/{run_id}/resume")
    assert response.status_code == 409
    assert await repo.claim_next_run("queued-competitor") is None
    assert calls == []
    assert not await repo.acquire_lease(run_id, "premature-replacement")
    assert await repo.renew_lease(run_id, "old-owner", seconds=0)
    async with _client() as client:
        accepted = await client.post(f"/api/runs/{run_id}/resume")
    assert accepted.status_code == 202
    await drain_inline(api.app)
    assert len(calls) == 1
    _, query, restored, source = calls[0]
    assert query == "original input"
    assert restored.max_rounds == 1 and restored.max_concurrency == 2
    assert source.checkpoint["scratch"]["verified_material"] == "retained evidence"
    assert await repo.get_run_status(run_id) == "done"
