"""Cross-layer recovery edges: API cancellation and executor setup failures."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from deep_research import api
from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.models import Report
from deep_research.observability import EventHub
from deep_research.orchestrator import DeepResearchAgent, create_initial_execution
from deep_research.persistence.repository import LeaseLostError
from tests.fakes import FakeLLM, FakeSearch
from tests.test_run_queue import repo as repo


async def test_fenced_attempt_does_not_publish_task_failure_or_overwrite_successor(
    repo, settings, monkeypatch
):
    settings = replace(settings, orchestration_mode="legacy", max_run_seconds=30)
    execution = create_initial_execution("Q", "quick", settings)
    run_id = await repo.create_run("Q", execution=execution, lease_owner="old")
    hub = EventHub()

    class Agent(DeepResearchAgent):
        async def _run_workflow(self, query, run_id):
            assert await repo.renew_lease(run_id, "old", seconds=0)
            assert await repo.acquire_lease(run_id, "successor")
            raise LeaseLostError("new worker owns the run")

    async def build_agent(self, settings, **kwargs):
        search = FakeSearch()
        return Agent(settings, llm=FakeLLM(), search_tool=search, **kwargs), search

    monkeypatch.setattr(RunExecutor, "build_agent", build_agent)
    await RunExecutor(ExecutionContext(repo=repo, live={run_id: hub})).execute(
        run_id, "Q", settings, initial_execution=execution, lease_owner="old"
    )
    assert await repo.get_run_status(run_id) == "running"
    assert not await repo.acquire_lease(run_id, "third")
    events = [event async for event in hub.stream()]
    assert any(event.data and event.data.get("reason") == "lease_lost" for event in events)
    assert not any(event.type in {"error", "done", "cancelled"} for event in events)


async def _callbacks_finished(app):
    # Task done callbacks can schedule a separate pre-start cancellation cleanup.
    await asyncio.sleep(0)
    while app.state.tasks:
        await asyncio.wait_for(
            asyncio.gather(*list(app.state.tasks), return_exceptions=True), timeout=5
        )


@pytest.mark.parametrize("tracked", [False, True])
@pytest.mark.parametrize("user_cancel", [False, True])
async def test_started_executor_cancellation_lease_semantics_match_api_tracking(
    repo, settings, monkeypatch, tracked, user_cancel
):
    settings = replace(settings, orchestration_mode="legacy", max_run_seconds=30)
    entered = asyncio.Event()

    class Agent(DeepResearchAgent):
        async def _run_workflow(self, query, run_id):
            entered.set()
            await asyncio.Event().wait()

    async def build_agent(self, settings, **kwargs):
        search = FakeSearch()
        return Agent(settings, llm=FakeLLM(), search_tool=search, **kwargs), search

    monkeypatch.setattr(RunExecutor, "build_agent", build_agent)
    execution = create_initial_execution("Q", "quick", settings)
    run_id = await repo.create_run("Q", execution=execution, lease_owner="original")
    live = {run_id: EventHub()}
    executor = RunExecutor(ExecutionContext(repo=repo, live=live))
    app = SimpleNamespace(
        state=SimpleNamespace(repo=repo, live=live, tasks=set(), executor=executor)
    )
    task = asyncio.create_task(
        executor.execute(run_id, "Q", settings, initial_execution=execution, lease_owner="original")
    )
    if tracked:
        api._track_run_task(app, run_id, task, lease_owner="original")
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        if user_cancel:
            await repo.request_cancel(run_id)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await _callbacks_finished(app)
        detail = await repo.get_run(run_id)
        assert detail.status == ("cancelled" if user_cancel else "running")
        assert await repo.acquire_lease(run_id, "successor") is user_cancel
        assert run_id not in live
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await _callbacks_finished(app)


@pytest.mark.parametrize("user_cancel", [False, True])
async def test_api_cancellation_before_executor_starts_releases_unused_lease(
    repo, settings, user_cancel
):
    execution = create_initial_execution("Q", "quick", settings)
    run_id = await repo.create_run("Q", execution=execution, lease_owner="original")
    live = {run_id: EventHub()}
    app = SimpleNamespace(state=SimpleNamespace(repo=repo, live=live, tasks=set()))

    async def never_started():
        raise AssertionError("pre-start cancellation must prevent execution")

    if user_cancel:
        await repo.request_cancel(run_id)
    task = asyncio.create_task(never_started())
    api._track_run_task(app, run_id, task, lease_owner="original")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await _callbacks_finished(app)
    assert await repo.acquire_lease(run_id, "successor")
    assert (await repo.get_run(run_id)).status == ("cancelled" if user_cancel else "pending")
    assert run_id not in live


async def test_transient_agent_setup_preserves_checkpoint_usage_before_tracer_exists(
    repo, settings, monkeypatch
):
    settings = replace(settings, orchestration_mode="legacy", max_run_seconds=30)
    execution = create_initial_execution("Q", "quick", settings)
    execution.checkpoint["scratch"]["_runtime_metrics"] = {
        "elapsed": 123.5,
        "total_tokens": 987,
        "estimated_tokens": 12,
    }
    run_id = await repo.create_run("Q", execution=execution, lease_owner="original")

    async def fail_before_agent(self, *_args, **_kwargs):
        raise httpx.ConnectError("temporary setup failure")

    monkeypatch.setattr(RunExecutor, "build_agent", fail_before_agent)
    await RunExecutor(ExecutionContext(repo=repo)).execute(
        run_id, "Q", settings, resume_execution=execution, lease_owner="original"
    )
    detail = await repo.get_run(run_id)
    assert detail.status == "running"
    scratch = detail.orchestration.checkpoint["scratch"]
    assert scratch["_runtime_metrics"] == {
        "elapsed": 123.5,
        "total_tokens": 987,
        "estimated_tokens": 12,
    }
    assert scratch["_attempt_elapsed_origin"] == 123.5
    assert scratch["_recovery"]["count"] == 1


async def test_direct_executor_creates_missing_initial_checkpoint(repo, settings, monkeypatch):
    settings = replace(settings, orchestration_mode="legacy", max_run_seconds=30)
    seen = []

    class Agent(DeepResearchAgent):
        async def _run_workflow(self, query, run_id):
            seen.append(self._initial_execution)
            return Report(query=query, markdown="Finished report")

    async def build_agent(self, settings, **kwargs):
        search = FakeSearch()
        return Agent(settings, llm=FakeLLM(), search_tool=search, **kwargs), search

    monkeypatch.setattr(RunExecutor, "build_agent", build_agent)
    run_id = await repo.create_run("Original query")
    await RunExecutor(ExecutionContext(repo=repo)).execute(
        run_id, "Original query", settings, workflow="quick"
    )
    detail = await repo.get_run(run_id)
    assert detail.status == "done"
    assert seen and seen[0].input["query"] == "Original query"
    assert seen[0].workflow_name == "quick"
    scratch = detail.orchestration.checkpoint["scratch"]
    assert scratch["_deadline_at"] > 0
    assert scratch["_task_deadline_at"] > scratch["_deadline_at"]
