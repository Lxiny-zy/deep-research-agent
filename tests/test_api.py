"""API 端点测试：httpx ASGITransport + 注入 InMemoryRepository，后台执行被 monkeypatch。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport

from deep_research import api
from deep_research import execution as execution_module
from deep_research.config import Settings
from deep_research.http import sse as sse_module
from deep_research.models import Report, ResearchPlan, SubQuestion
from deep_research.observability import Event, EventHub, Tracer
from deep_research.orchestration import OrchestrationRuntime
from deep_research.orchestrator import RUN_SETTINGS_CHECKPOINT_KEY
from deep_research.persistence.memory_repository import InMemoryRepository


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test")


async def _assert_hub_closed(hub: EventHub) -> None:
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(hub.stream()), timeout=0.1)


@pytest.fixture
def repo(monkeypatch) -> InMemoryRepository:
    r = InMemoryRepository()
    api.app.state.settings = Settings()  # lifespan 未在 ASGITransport 下触发，手动注入
    api.app.state.repo = r
    api.app.state.live = {}
    api.app.state.tasks = set()
    api.app.state.run_tasks = {}
    api.app.state.cancellation_requested = set()
    api.app.state.run_admission = api.RunAdmission(
        api.app.state.settings.max_active_runs, api.app.state.settings.max_queued_runs
    )
    api.app.state.config_lock = asyncio.Lock()
    api.app.state.run_admission = api.RunAdmission(
        api.app.state.settings.max_active_runs,
        api.app.state.settings.max_queued_runs,
    )

    async def _noop(
        app,
        run_id,
        query,
        settings,
        workflow=None,
        resume_execution=None,
        lease_owner=None,
        initial_execution=None,
        requested_workflow=None,
    ):  # 不跑真实 agent
        return None

    monkeypatch.setattr(api, "_execute", _noop)
    return r


@pytest.mark.asyncio
async def test_healthz():
    async with _client() as c:
        resp = await c.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_liveness_and_readiness(repo):
    async with _client() as c:
        live = await c.get("/livez")
        ready = await c.get("/readyz")
    assert live.json() == {"status": "ok"}
    assert ready.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_request_id_and_metrics_endpoint(repo):
    async with _client() as c:
        health = await c.get("/healthz", headers={"X-Request-ID": "test-request-1"})
        metrics_response = await c.get("/metrics")

    assert health.headers["X-Request-ID"] == "test-request-1"
    assert health.headers["X-Content-Type-Options"] == "nosniff"
    assert health.headers["X-Frame-Options"] == "DENY"
    assert health.headers["Referrer-Policy"] == "same-origin"
    assert "default-src 'self'" in health.headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in health.headers
    assert metrics_response.status_code == 200
    assert metrics_response.headers["content-type"].startswith("text/plain")
    assert "deep_research_http_requests_total" in metrics_response.text
    assert 'route="/healthz"' in metrics_response.text


@pytest.mark.asyncio
async def test_https_responses_include_hsts(repo):
    async with httpx.AsyncClient(
        transport=ASGITransport(app=api.app), base_url="https://test"
    ) as c:
        response = await c.get("/healthz")

    assert response.headers["Strict-Transport-Security"] == ("max-age=31536000; includeSubDomains")


@pytest.mark.asyncio
async def test_trusted_forwarded_https_responses_include_hsts(repo, monkeypatch):
    monkeypatch.setenv("APP_TRUST_PROXY", "true")
    async with _client() as c:
        response = await c.get("/healthz", headers={"X-Forwarded-Proto": "https"})

    assert response.headers["Strict-Transport-Security"] == ("max-age=31536000; includeSubDomains")


@pytest.mark.asyncio
async def test_create_run(repo):
    async with _client() as c:
        resp = await c.post("/api/runs", json={"query": "Q"})
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    detail = await repo.get_run(run_id)
    assert detail is not None
    assert detail.query == "Q"
    assert detail.orchestration is not None
    assert await repo.acquire_lease(run_id, "another-worker") is False


@pytest.mark.asyncio
async def test_create_run_rejects_when_admission_capacity_is_full(repo, monkeypatch):
    api.app.state.settings = Settings(max_active_runs=1, max_queued_runs=0)
    api.app.state.run_admission = api.RunAdmission(1, 0)
    monkeypatch.setattr(api, "_check_rate_limit", lambda request: None)
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_execute(*args, **kwargs):  # type: ignore[no-untyped-def]
        started.set()
        await release.wait()

    monkeypatch.setattr(api, "_execute", blocked_execute)
    async with _client() as c:
        first = asyncio.create_task(c.post("/api/runs", json={"query": "first"}))
        await started.wait()
        second = await c.post("/api/runs", json={"query": "second"})
        assert second.status_code == 503
        release.set()
        assert (await first).status_code == 202
    await asyncio.gather(*list(api.app.state.tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_create_request_releases_admission(repo, monkeypatch) -> None:
    admission = api.RunAdmission(1, 0)
    api.app.state.run_admission = admission
    persistence_started = asyncio.Event()

    async def blocked_create_run_once(*args, **kwargs):  # type: ignore[no-untyped-def]
        persistence_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(repo, "create_run_once", blocked_create_run_once)
    async with _client() as client:
        request_task = asyncio.create_task(client.post("/api/runs", json={"query": "cancel me"}))
        await persistence_started.wait()
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task

    assert admission.active == 0
    assert admission.queued == 0
    assert await repo.list_runs() == []


@pytest.mark.asyncio
async def test_create_run_idempotency_replays_and_conflicts(repo):
    headers = {"Idempotency-Key": "create-1"}
    async with _client() as c:
        first = await c.post("/api/runs", json={"query": "Q"}, headers=headers)
        replay = await c.post("/api/runs", json={"query": "Q"}, headers=headers)
        conflict = await c.post("/api/runs", json={"query": "different"}, headers=headers)

    assert first.status_code == replay.status_code == 202
    assert first.json()["run_id"] == replay.json()["run_id"]
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert len(await repo.list_runs()) == 1


@pytest.mark.asyncio
async def test_blank_idempotency_key_is_treated_as_absent(repo):
    headers = {"Idempotency-Key": "   "}
    async with _client() as c:
        first = await c.post("/api/runs", json={"query": "Q"}, headers=headers)
        second = await c.post("/api/runs", json={"query": "Q"}, headers=headers)

    assert first.status_code == second.status_code == 202
    assert first.json()["run_id"] != second.json()["run_id"]


@pytest.mark.asyncio
async def test_cancel_endpoint_is_idempotent_for_active_run(repo):
    run_id = await repo.create_run("cancel me")
    async with _client() as c:
        first = await c.post(f"/api/runs/{run_id}/cancel")
        second = await c.post(f"/api/runs/{run_id}/cancel")
    assert first.status_code == second.status_code == 202
    assert first.json()["status"] == second.json()["status"] == "cancelling"


@pytest.mark.asyncio
async def test_task_cancelled_before_start_reaches_cancelled(repo):
    owner = "prestart-owner"
    execution = OrchestrationRuntime().start("deep", {"query": "cancel before start"})
    run_id = await repo.create_run("cancel before start", execution=execution, lease_owner=owner)
    api.app.state.live[run_id] = EventHub()
    assert await repo.request_cancel(run_id) == "cancelling"

    async def never_started() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(never_started())
    api.app.state.cancellation_requested = {run_id}
    api._track_run_task(api.app, run_id, task, lease_owner=owner)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    for _ in range(10):
        if await repo.get_run_status(run_id) == "cancelled":
            break
        await asyncio.sleep(0)

    assert await repo.get_run_status(run_id) == "cancelled"
    events = await repo.get_events(run_id)
    assert events[-1].type == "cancelled"
    assert run_id not in api.app.state.live
    assert run_id not in api.app.state.cancellation_requested


@pytest.mark.asyncio
async def test_prestart_shutdown_cancellation_releases_lease_and_hub(repo) -> None:
    owner = "prestart-shutdown-owner"
    execution = _recoverable_execution("shutdown before start")
    run_id = await repo.create_run("shutdown before start", execution=execution, lease_owner=owner)
    hub = EventHub()
    api.app.state.live[run_id] = hub
    admission = api.RunAdmission(1, 0)
    slot = admission.acquire()

    task = asyncio.create_task(
        api._execute_with_admission(
            slot,
            api.app,
            run_id,
            "shutdown before start",
            Settings(),
            "deep",
            None,
            owner,
        )
    )
    api._track_run_task(api.app, run_id, task, lease_owner=owner, admission=slot)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    cleanup_tasks = list(api.app.state.cleanup_tasks)
    if cleanup_tasks:
        await asyncio.gather(*cleanup_tasks, return_exceptions=True)

    assert await repo.get_run_status(run_id) == "pending"
    assert await repo.acquire_lease(run_id, "replacement-worker") is True
    assert run_id not in api.app.state.live
    assert admission.active == 0
    await _assert_hub_closed(hub)


@pytest.mark.asyncio
async def test_handled_task_cancellation_clears_requested_marker(repo) -> None:
    run_id = await repo.create_run("handled cancellation")
    api.app.state.cancellation_requested = {run_id}

    async def handle_cancellation() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return

    task = asyncio.create_task(handle_cancellation())
    api._track_run_task(api.app, run_id, task)
    await asyncio.sleep(0)
    task.cancel()
    await task
    await asyncio.sleep(0)

    assert task.cancelled() is False
    assert run_id not in api.app.state.cancellation_requested


@pytest.mark.asyncio
async def test_list_detail_events_and_404(repo):
    run_id = await repo.create_run("历史问题")
    await repo.save_plan(
        run_id, ResearchPlan(interpretation="i", sub_questions=[SubQuestion(question="a")])
    )
    await repo.save_report(
        run_id, Report(query="历史问题", markdown="# R", citations=["https://a.com"])
    )
    await repo.save_events(run_id, [Event(stage="PLANNER", type="start", message="m")])
    await repo.finalize(run_id, elapsed=1.0, total_tokens=10)

    async with _client() as c:
        lst = await c.get("/api/runs")
        detail = await c.get(f"/api/runs/{run_id}")
        evs = await c.get(f"/api/runs/{run_id}/events")
        missing = await c.get("/api/runs/does-not-exist")

    assert any(item["id"] == run_id for item in lst.json())
    assert detail.json()["report"]["markdown"] == "# R"
    assert detail.json()["metrics"]["cited_sources"] == 1
    assert detail.json()["events"][0]["stage"] == "PLANNER"
    assert evs.json()[0]["stage"] == "PLANNER"
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_set_tags_returns_enriched_run_detail(repo) -> None:
    run_id = await repo.create_run("tagged")
    await repo.save_report(run_id, Report(query="tagged", markdown="# R", citations=[]))
    await repo.save_events(run_id, [Event(stage="PLANNER", type="start", message="m")])

    async with _client() as client:
        response = await client.put(f"/api/runs/{run_id}/tags", json={"tags": ["reviewed"]})

    assert response.status_code == 200
    body = response.json()
    assert body["tags"] == ["reviewed"]
    assert body["events"][0]["stage"] == "PLANNER"
    assert body["metrics"] is not None


@pytest.mark.asyncio
async def test_stream_replays_from_db(repo):
    run_id = await repo.create_run("Q")
    await repo.save_events(
        run_id,
        [Event(stage="PLANNER", type="start"), Event(stage="ORCHESTRATOR", type="done")],
    )
    await repo.finalize(run_id, elapsed=1.0, total_tokens=1)
    async with _client() as c:
        resp = await c.get(f"/api/runs/{run_id}/stream")
    assert resp.status_code == 200
    assert "PLANNER" in resp.text
    assert "done" in resp.text


@pytest.mark.asyncio
async def test_stream_resumes_after_last_event_id(repo):
    run_id = await repo.create_run("Q")
    await repo.append_events(
        run_id,
        [
            Event(stage="PLANNER", type="start", message="first"),
            Event(stage="ORCHESTRATOR", type="done", message="second"),
        ],
    )
    await repo.finalize(run_id, elapsed=1, total_tokens=1)
    async with _client() as c:
        response = await c.get(f"/api/runs/{run_id}/stream", headers={"Last-Event-ID": "0"})
    assert "first" not in response.text
    assert "second" in response.text
    assert "id: 1" in response.text


@pytest.mark.asyncio
async def test_remote_stream_waits_for_durable_terminal_events(repo, monkeypatch) -> None:
    run_id = await repo.create_run("remote")
    await repo.set_status(run_id, "running")
    await repo.save_events(
        run_id,
        [Event(stage="ORCHESTRATOR", type="error", message="old attempt")],
    )
    remote_app = SimpleNamespace(state=SimpleNamespace(repo=repo, live={}))
    monkeypatch.setattr(sse_module, "_REMOTE_STREAM_POLL_SECONDS", 0.001)
    monkeypatch.setattr(sse_module, "_REMOTE_STREAM_TERMINAL_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(sse_module, "_SSE_HEARTBEAT_SECONDS", 60.0)

    async def consume() -> list[str]:
        return [chunk async for chunk in api._stream_run_sse(remote_app, run_id)]

    stream_task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    assert not stream_task.done()

    # Production finalizes the run before save_events executes in finally.
    await repo.finalize(run_id, elapsed=1.0, total_tokens=1)
    await asyncio.sleep(0.01)
    await repo.save_events(
        run_id,
        [
            Event(stage="PLANNER", type="info", message="new progress"),
            Event(stage="ORCHESTRATOR", type="done", message="new success"),
        ],
    )

    payload = "".join(await stream_task)
    assert "new progress" in payload
    assert "new success" in payload
    assert "old attempt" not in payload
    assert remote_app.state.live == {}


@pytest.mark.asyncio
async def test_remote_stream_synthesizes_missing_terminal_event(repo, monkeypatch) -> None:
    run_id = await repo.create_run("failed remotely")
    await repo.set_status(run_id, "error")
    remote_app = SimpleNamespace(state=SimpleNamespace(repo=repo, live={}))
    monkeypatch.setattr(sse_module, "_REMOTE_STREAM_POLL_SECONDS", 0.001)
    monkeypatch.setattr(sse_module, "_REMOTE_STREAM_TERMINAL_GRACE_SECONDS", 0.005)

    payload = "".join([chunk async for chunk in api._stream_run_sse(remote_app, run_id)])

    assert '"type":"error"' in payload
    assert '"status":"error"' in payload


@pytest.mark.asyncio
async def test_remote_stream_drains_all_pages_before_terminal_grace(repo, monkeypatch) -> None:
    run_id = await repo.create_run("large remote replay")
    progress_count = api._SSE_EVENT_BATCH_SIZE * 2 + 1
    await repo.save_events(
        run_id,
        [
            Event(stage="RESEARCHER", type="info", message=f"progress-{index}")
            for index in range(progress_count)
        ]
        + [Event(stage="ORCHESTRATOR", type="done", message="durable-success")],
    )
    await repo.finalize(run_id, elapsed=1.0, total_tokens=1)
    remote_app = SimpleNamespace(state=SimpleNamespace(repo=repo, live={}))
    monkeypatch.setattr(sse_module, "_REMOTE_STREAM_POLL_SECONDS", 0.0)
    monkeypatch.setattr(sse_module, "_REMOTE_STREAM_TERMINAL_GRACE_SECONDS", 0.0)

    payload = "".join([chunk async for chunk in api._stream_run_sse(remote_app, run_id)])

    assert f"progress-{progress_count - 1}" in payload
    assert "durable-success" in payload
    assert f"id: {progress_count}" in payload


@pytest.mark.asyncio
async def test_remote_stream_does_not_replay_same_type_terminal_from_old_attempt(
    monkeypatch,
) -> None:
    class BarrierRepository(InMemoryRepository):
        def __init__(self) -> None:
            super().__init__()
            self.first_status_read = asyncio.Event()
            self.release_first_status = asyncio.Event()
            self.status_reads = 0

        async def get_run_status(self, run_id: str) -> str | None:
            status = await super().get_run_status(run_id)
            self.status_reads += 1
            if self.status_reads == 1:
                self.first_status_read.set()
                await self.release_first_status.wait()
            return status

    repo = BarrierRepository()
    owner = "new-attempt"
    execution = _recoverable_execution("retry failure")
    run_id = await repo.create_run("retry failure", execution=execution, lease_owner=owner)
    await repo.set_status(run_id, "error", lease_owner=owner)
    await repo.save_events(
        run_id,
        [Event(stage="ORCHESTRATOR", type="error", message="old failure")],
        lease_owner=owner,
    )
    remote_app = SimpleNamespace(state=SimpleNamespace(repo=repo, live={}))
    monkeypatch.setattr(sse_module, "_REMOTE_STREAM_POLL_SECONDS", 0.001)
    monkeypatch.setattr(sse_module, "_REMOTE_STREAM_TERMINAL_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(sse_module, "_SSE_HEARTBEAT_SECONDS", 60.0)

    async def consume() -> str:
        return "".join([chunk async for chunk in api._stream_run_sse(remote_app, run_id)])

    stream_task = asyncio.create_task(consume())
    await repo.first_status_read.wait()
    await repo.prepare_resume(run_id, lease_owner=owner)
    repo.release_first_status.set()
    await asyncio.sleep(0.01)
    assert not stream_task.done()
    await repo.set_status(run_id, "error", lease_owner=owner)
    await asyncio.sleep(0.01)
    assert not stream_task.done()
    await repo.save_events(
        run_id,
        [Event(stage="ORCHESTRATOR", type="error", message="new failure")],
        lease_owner=owner,
    )

    payload = await stream_task
    assert "new failure" in payload
    assert "old failure" not in payload


@pytest.mark.asyncio
async def test_legacy_research_creates_persisted_run_and_streams_it(repo, monkeypatch):
    runtime_settings = Settings(llm_model="saved-model", llm_api_key="saved-key")
    api.app.state.settings = runtime_settings
    captured = {}

    async def fake_execute(
        app,
        run_id,
        query,
        settings,
        workflow=None,
        resume_execution=None,
        lease_owner=None,
        initial_execution=None,
        requested_workflow=None,
    ):
        captured["settings"] = settings
        captured["query"] = query
        event = Event(stage="ORCHESTRATOR", type="done", message="finished")
        persisted = await app.state.repo.append_events(run_id, [event], lease_owner=lease_owner)
        await app.state.repo.finalize(run_id, elapsed=0.1, total_tokens=1, lease_owner=lease_owner)
        hub = app.state.live.get(run_id)
        if hub is not None:
            for stored_event in persisted:
                hub.publish(stored_event)
            hub.close()
            app.state.live.pop(run_id, None)
        if lease_owner is not None:
            await app.state.repo.release_lease(run_id, lease_owner)

    monkeypatch.setattr(api, "_execute", fake_execute)
    monkeypatch.setattr(api, "_check_rate_limit", lambda request: None)

    async with _client() as c:
        resp = await c.get("/api/research", params={"q": "Q"})

    assert resp.status_code == 200
    run_id = resp.headers["X-Run-ID"]
    detail = await repo.get_run(run_id)
    assert detail is not None
    assert detail.query == "Q"
    assert detail.status == "done"
    assert '"type":"done"' in resp.text
    assert captured["query"] == "Q"
    assert captured["settings"] is runtime_settings


@pytest.mark.asyncio
async def test_build_agent_uses_search_key_pool(repo, monkeypatch):
    """持久化执行构造 agent 时，key 池非空则注入 TavilyKeyPoolSearch。"""
    from deep_research.tools.tavily_pool import TavilyKeyPoolSearch

    class FakeCatalog:
        async def active_keys(self):
            return ["tvly-primary", "tvly-backup"]

    monkeypatch.setattr(api.app.state, "catalog", FakeCatalog(), raising=False)
    captured = {}

    class FakeAgent:
        def __init__(self, settings, **kwargs):
            captured.update(kwargs)

        async def run_stream(self, query):
            yield Event(stage="ORCHESTRATOR", type="done")

        async def aclose(self):
            return None

    monkeypatch.setattr(execution_module, "DeepResearchAgent", FakeAgent)
    agent, search_tool = await api._build_agent(api.app, api.app.state.settings)
    try:
        assert isinstance(search_tool, TavilyKeyPoolSearch)
        assert captured["search_tool"] is search_tool
        assert captured["catalog_repo"] is api.app.state.catalog
    finally:
        await agent.aclose()
        if search_tool is not None:
            await search_tool.aclose()


@pytest.mark.asyncio
async def test_delete_run(repo):
    run_id = await repo.create_run("待删")
    await repo.set_status(run_id, "error")
    async with _client() as c:
        ok = await c.delete(f"/api/runs/{run_id}")
        missing = await c.delete(f"/api/runs/{run_id}")
    assert ok.status_code == 204
    assert await repo.get_run(run_id) is None
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_delete_running_run_conflict(repo):
    run_id = await repo.create_run("进行中")
    api.app.state.live[run_id] = object()  # 模拟进行中
    async with _client() as c:
        resp = await c.delete(f"/api/runs/{run_id}")
    api.app.state.live.pop(run_id, None)
    assert resp.status_code == 409
    assert await repo.get_run(run_id) is not None  # 未被删除


@pytest.mark.asyncio
async def test_delete_rejects_lease_held_by_another_instance(repo) -> None:
    run_id = await repo.create_run(
        "remote worker",
        execution=_recoverable_execution("remote worker"),
        lease_owner="remote-owner",
    )

    async with _client() as client:
        single = await client.delete(f"/api/runs/{run_id}")
        batch = await client.post("/api/runs/batch_delete", json={"ids": [run_id]})

    assert single.status_code == 409
    assert batch.json() == {"deleted": 0, "skipped": 1}
    assert await repo.get_run(run_id) is not None


@pytest.mark.asyncio
async def test_batch_delete_skips_running(repo):
    a = await repo.create_run("A")
    b = await repo.create_run("B")
    c_id = await repo.create_run("C")
    await repo.set_status(a, "error")
    await repo.set_status(b, "error")
    api.app.state.live[c_id] = object()  # C 进行中，应跳过
    async with _client() as c:
        resp = await c.post("/api/runs/batch_delete", json={"ids": [a, b, c_id]})
    api.app.state.live.pop(c_id, None)
    body = resp.json()
    assert body == {"deleted": 2, "skipped": 1}
    assert await repo.get_run(a) is None
    assert await repo.get_run(c_id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "running"])
async def test_delete_protects_legacy_active_run_without_orchestration(repo, status):
    run_id = await repo.create_run(f"legacy {status}")
    await repo.set_status(run_id, status)

    async with _client() as client:
        single = await client.delete(f"/api/runs/{run_id}")
        batch = await client.post("/api/runs/batch_delete", json={"ids": [run_id]})

    assert single.status_code == 409
    assert batch.json() == {"deleted": 0, "skipped": 1}
    detail = await repo.get_run(run_id)
    assert detail is not None
    assert detail.orchestration is None
    assert detail.status == status


@pytest.mark.asyncio
async def test_tags_put_and_list_and_filter(repo):
    run_id = await repo.create_run("打标签")
    async with _client() as c:
        put = await c.put(f"/api/runs/{run_id}/tags", json={"tags": ["t1", "t1", "t2"]})
        tags = await c.get("/api/tags")
        listed = await c.get("/api/runs", params={"tag": "t1"})
        empty = await c.get("/api/runs", params={"tag": "nope"})
    assert put.status_code == 200
    assert sorted(put.json()["tags"]) == ["t1", "t2"]
    assert {t["tag"]: t["count"] for t in tags.json()} == {"t1": 1, "t2": 1}
    assert any(x["id"] == run_id for x in listed.json())
    assert empty.json() == []


@pytest.mark.asyncio
async def test_tags_put_404(repo):
    async with _client() as c:
        resp = await c.put("/api/runs/does-not-exist/tags", json={"tags": ["x"]})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_runs_status_filter(repo):
    a = await repo.create_run("已完成的")
    await repo.create_run("排队的")
    await repo.finalize(a, elapsed=1.0, total_tokens=5)
    async with _client() as c:
        done = await c.get("/api/runs", params={"status": "done"})
    ids = [x["id"] for x in done.json()]
    assert ids == [a]


@pytest.mark.asyncio
async def test_config_get_masks_secrets(repo):
    api.app.state.settings = Settings(llm_api_key="sk-aaaa1234", llm_model="m1")
    async with _client() as c:
        resp = await c.get("/api/config")
    body = resp.json()
    assert body["llm_model"] == "m1"
    assert body["llm_api_key_set"] is True
    assert body["llm_api_key_hint"] == "…1234"
    assert body["require_corroboration"] is False
    assert body["fulltext_enabled"] is True
    assert body["fulltext_max_chars"] == 12_000
    assert "sk-aaaa1234" not in resp.text


@pytest.mark.asyncio
async def test_api_key_accepts_bearer_but_not_query_parameter(repo):
    api.app.state.settings = Settings(api_key="service-secret")
    async with _client() as c:
        query = await c.get("/api/config", params={"api_key": "service-secret"})
        bearer = await c.get("/api/config", headers={"Authorization": "Bearer service-secret"})
    assert query.status_code == 401
    assert bearer.status_code == 200


@pytest.mark.asyncio
async def test_config_put_keeps_empty_secret(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(tmp_path / "cfg.json"))
    monkeypatch.setenv("LLM_API_KEY", "sk-env-key-9999")
    async with _client() as c:
        resp = await c.put("/api/config", json={"llm_model": "new-model", "max_rounds": 3})
    assert resp.status_code == 200
    assert api.app.state.settings.llm_model == "new-model"
    assert api.app.state.settings.max_rounds == 3
    assert api.app.state.settings.llm_api_key == "sk-env-key-9999"  # 空密钥保留 env 值


@pytest.mark.asyncio
async def test_config_put_persists_corroboration_gate(repo, tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.json"
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(cfg))
    async with _client() as c:
        resp = await c.put("/api/config", json={"require_corroboration": True})
    assert resp.status_code == 200
    assert resp.json()["require_corroboration"] is True
    assert api.app.state.settings.require_corroboration is True
    assert json.loads(cfg.read_text(encoding="utf-8"))["require_corroboration"] is True


@pytest.mark.asyncio
async def test_config_put_persists_fulltext_settings(repo, tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.json"
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(cfg))
    async with _client() as c:
        resp = await c.put(
            "/api/config",
            json={"fulltext_enabled": False, "fulltext_max_chars": 20_000},
        )
    assert resp.status_code == 200
    assert resp.json()["fulltext_enabled"] is False
    assert resp.json()["fulltext_max_chars"] == 20_000
    assert api.app.state.settings.fulltext_enabled is False
    assert api.app.state.settings.fulltext_max_chars == 20_000
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved["fulltext_enabled"] is False
    assert saved["fulltext_max_chars"] == 20_000


@pytest.mark.asyncio
async def test_config_put_sets_secret_without_persisting_plaintext(repo, tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.json"
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(cfg))
    async with _client() as c:
        resp = await c.put("/api/config", json={"llm_api_key": "sk-new-1234"})
    assert resp.status_code == 200
    assert api.app.state.settings.llm_api_key == "sk-new-1234"
    assert resp.json()["llm_api_key_hint"] == "…1234"
    assert "sk-new-1234" not in resp.text  # 响应已脱敏
    assert "llm_api_key" not in json.loads(cfg.read_text(encoding="utf-8"))
    assert "sk-new-1234" not in cfg.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_config_put_validates_range(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(tmp_path / "cfg.json"))
    async with _client() as c:
        resp = await c.put("/api/config", json={"max_concurrency": 0})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_config_put_rejects_ambiguous_provider_hostname(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(tmp_path / "cfg.json"))
    async with _client() as c:
        resp = await c.put("/api/config", json={"llm_base_url": "https://127.0.0.1%20/v1"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_config_put_rejects_explicit_null(repo, tmp_path, monkeypatch):
    """显式 null 直接 422，且不得写入持久化文件、不得改内存 Settings。"""
    cfg = tmp_path / "cfg.json"
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(cfg))
    before = api.app.state.settings
    async with _client() as c:
        model_null = await c.put("/api/config", json={"llm_model": None})
        rounds_null = await c.put("/api/config", json={"max_rounds": None})
    assert model_null.status_code == 422
    assert rounds_null.status_code == 422
    assert not cfg.exists()  # 校验失败不落盘
    assert api.app.state.settings is before  # 内存状态未被提交


@pytest.mark.asyncio
async def test_config_get_self_heals_polluted_overrides(repo, tmp_path, monkeypatch):
    """旧版本污染（overrides 含 llm_model: null）下 GET 仍返回 200 并清洗文件。"""
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"llm_model": None, "max_rounds": 3}), encoding="utf-8")
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(cfg))
    api.app.state.settings = replace(Settings(), llm_model=None)  # 内存已被污染
    async with _client() as c:
        resp = await c.get("/api/config")
    body = resp.json()
    assert resp.status_code == 200
    assert body["llm_model"]  # 自愈回默认模型
    assert body["max_rounds"] == 3  # 合法覆盖保留
    assert api.app.state.settings.llm_model  # 内存状态已恢复
    assert "llm_model" not in json.loads(cfg.read_text(encoding="utf-8"))  # 文件已清洗


@pytest.mark.asyncio
async def test_config_put_cleans_polluted_overrides(repo, tmp_path, monkeypatch):
    """PUT 合法字段时顺带清掉旧污染文件里的 None 值，之后不再 500。"""
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"llm_model": None}), encoding="utf-8")
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(cfg))
    async with _client() as c:
        resp = await c.put("/api/config", json={"max_rounds": 2})
    assert resp.status_code == 200
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved == {"max_rounds": 2}  # None 污染键已被丢弃
    assert api.app.state.settings.llm_model  # 未被 None 覆盖


@pytest.mark.asyncio
async def test_config_put_persistence_failure_keeps_memory_state(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(tmp_path / "cfg.json"))
    before = api.app.state.settings

    def fail_save(_overrides):
        raise OSError("disk unavailable")

    monkeypatch.setattr(api.runtime_config, "save_overrides", fail_save)
    async with _client() as c:
        resp = await c.put("/api/config", json={"llm_model": "not-committed"})

    assert resp.status_code == 500
    assert api.app.state.settings is before


@pytest.mark.asyncio
async def test_create_task_failure_releases_admission_and_run_lease(repo, monkeypatch):
    admission = api.RunAdmission(1, 0)
    api.app.state.run_admission = admission
    hub = EventHub()
    monkeypatch.setattr(api, "EventHub", lambda: hub)

    def fail_create_task(coro):  # type: ignore[no-untyped-def]
        coro.close()
        raise RuntimeError("scheduler unavailable")

    monkeypatch.setattr(api.asyncio, "create_task", fail_create_task)
    async with _client() as c:
        with pytest.raises(RuntimeError, match="scheduler unavailable"):
            await c.post("/api/runs", json={"query": "cannot schedule"})

    assert admission.active == 0
    assert admission.queued == 0
    runs = await repo.list_runs()
    assert len(runs) == 1
    assert runs[0].status == "error"
    assert await repo.acquire_lease(runs[0].id, "replacement-worker") is True
    assert runs[0].id not in api.app.state.live
    await _assert_hub_closed(hub)


@pytest.mark.asyncio
async def test_orphan_task_creation_failure_releases_all_leases(monkeypatch):
    repo = InMemoryRepository()
    run_id = await repo.create_run(
        "recover cannot schedule",
        execution=_recoverable_execution("recover cannot schedule"),
    )
    admission = api.RunAdmission(1, 0)
    hub = EventHub()
    monkeypatch.setattr(api, "EventHub", lambda: hub)
    app = SimpleNamespace(
        state=SimpleNamespace(
            repo=repo,
            live={},
            tasks=set(),
            settings=Settings(max_active_runs=1, max_queued_runs=0),
            run_admission=admission,
        )
    )

    def fail_create_task(coro):  # type: ignore[no-untyped-def]
        coro.close()
        raise RuntimeError("scheduler unavailable")

    monkeypatch.setattr(api.asyncio, "create_task", fail_create_task)
    await api._recover_orphaned_runs(app, app.state.settings)

    assert admission.active == 0
    assert admission.queued == 0
    assert run_id not in app.state.live
    assert await repo.acquire_lease(run_id, "replacement-worker") is True
    await _assert_hub_closed(hub)


@pytest.mark.asyncio
async def test_queued_wrapper_setup_failure_releases_admission_and_durable_lease(monkeypatch):
    repo = InMemoryRepository()
    owner = "queued-owner"
    run_id = await repo.create_run(
        "queued setup failure",
        execution=_recoverable_execution("queued setup failure"),
        lease_owner=owner,
    )
    admission = api.RunAdmission(1, 0)
    slot = admission.acquire()
    hub = EventHub()
    app = SimpleNamespace(state=SimpleNamespace(repo=repo, live={run_id: hub}))

    def fail_create_task(coro):  # type: ignore[no-untyped-def]
        coro.close()
        raise RuntimeError("scheduler unavailable")

    monkeypatch.setattr(api.asyncio, "create_task", fail_create_task)
    with pytest.raises(RuntimeError, match="scheduler unavailable"):
        await api._execute_with_admission(
            slot,
            app,
            run_id,
            "queued setup failure",
            Settings(),
            "deep",
            None,
            owner,
            lease_owner=owner,
        )

    assert admission.active == 0
    assert await repo.acquire_lease(run_id, "replacement-worker") is True
    assert run_id not in app.state.live
    await _assert_hub_closed(hub)


@pytest.mark.asyncio
async def test_resume_task_creation_failure_releases_resources(repo, monkeypatch):
    run_id = await repo.create_run(
        "resume cannot schedule",
        execution=_recoverable_execution("resume cannot schedule"),
    )
    admission = api.RunAdmission(1, 0)
    api.app.state.run_admission = admission
    hub = EventHub()
    monkeypatch.setattr(api, "EventHub", lambda: hub)

    def fail_create_task(coro):  # type: ignore[no-untyped-def]
        coro.close()
        raise RuntimeError("scheduler unavailable")

    monkeypatch.setattr(api.asyncio, "create_task", fail_create_task)
    async with _client() as c:
        with pytest.raises(RuntimeError, match="scheduler unavailable"):
            await c.post(f"/api/runs/{run_id}/resume")

    assert admission.active == 0
    assert admission.queued == 0
    assert run_id not in api.app.state.live
    assert await repo.acquire_lease(run_id, "replacement-worker") is True
    await _assert_hub_closed(hub)


@pytest.mark.asyncio
async def test_close_live_hub_wakes_subscribers_and_preserves_replacement() -> None:
    run_id = "replaced-hub"
    old_hub = EventHub()
    replacement = EventHub()
    app = SimpleNamespace(state=SimpleNamespace(live={run_id: old_hub}))
    stream = old_hub.stream()
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    app.state.live[run_id] = replacement

    api._close_live_hub(app, run_id, old_hub)

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(pending, timeout=0.1)
    assert app.state.live[run_id] is replacement
    replacement.close()


@pytest.mark.asyncio
async def test_events_unknown_run_returns_404(repo):
    """与 /stream 语义一致：未知 run 404，而非 200 + 空列表。"""
    async with _client() as c:
        resp = await c.get("/api/runs/does-not-exist/events")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unknown_api_route_is_authenticated_json_404(repo):
    api.app.state.settings.api_key = "secret"
    try:
        async with _client() as c:
            unauthenticated = await c.get("/api/not-a-route")
            missing = await c.get("/api/not-a-route", headers={"Authorization": "Bearer secret"})
        assert unauthenticated.status_code == 401
        assert missing.status_code == 404
        assert missing.headers["content-type"].startswith("application/json")
    finally:
        api.app.state.settings.api_key = ""


@pytest.mark.asyncio
async def test_live_stream_emits_heartbeat_during_silence(monkeypatch) -> None:
    """live 分支长静默时定期发 SSE 注释行保活，事件到达后正常透传。"""
    run_id = "live-heartbeat"
    hub = EventHub()
    app = SimpleNamespace(state=SimpleNamespace(repo=InMemoryRepository(), live={run_id: hub}))
    monkeypatch.setattr(sse_module, "_SSE_HEARTBEAT_SECONDS", 0.01)
    chunks: list[str] = []

    async def consume() -> None:
        async for chunk in api._stream_run_sse(app, run_id):
            chunks.append(chunk)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.1)  # 无事件间隙：应已发出至少一条心跳
    assert any(chunk.startswith(": keep-alive") for chunk in chunks)
    hub.publish(Event(stage="ORCHESTRATOR", type="done", message="ok"))
    hub.close()
    await asyncio.wait_for(task, timeout=1.0)
    assert any('"type":"done"' in chunk for chunk in chunks)


@pytest.mark.asyncio
async def test_live_stream_recovers_from_slow_subscriber_overflow(monkeypatch) -> None:
    """A bounded hub queue must fall back to durable events after a slow read."""
    repo = InMemoryRepository()
    run_id = await repo.create_run("slow client")
    await repo.set_status(run_id, "running")
    hub = EventHub()
    monkeypatch.setattr(hub, "_QUEUE_MAXSIZE", 2)
    monkeypatch.setattr(sse_module, "_SSE_HEARTBEAT_SECONDS", 60.0)
    monkeypatch.setattr(sse_module, "_REMOTE_STREAM_POLL_SECONDS", 0.001)
    app = SimpleNamespace(state=SimpleNamespace(repo=repo, live={run_id: hub}))

    stream = api._stream_run_sse(app, run_id)
    first_read = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    first = Event(stage="PLANNER", type="info", message="first")
    hub.publish(first)
    first_frame = await first_read
    assert "first" in first_frame

    later = [Event(stage="RESEARCHER", type="info", message=f"later-{i}") for i in range(3)]
    terminal = Event(stage="ORCHESTRATOR", type="done", message="finished")
    # Persist after the first live frame to exercise the recovery cursor.
    await repo.append_events(run_id, [first, *later, terminal])
    await repo.set_status(run_id, "done")
    for event in later + [terminal]:
        hub.publish(event)

    frames = [first_frame]
    async for frame in stream:
        frames.append(frame)
    payload = "".join(frames)
    assert all(message in payload for message in ("later-0", "later-1", "later-2", "finished"))
    assert payload.count('"message":"first"') == 1


def test_rate_limiter_prunes_stale_entries(monkeypatch) -> None:
    """key 总量超上限时清理窗口外条目（含空条目），内存不无界增长。"""
    limiter = api._RateLimiter(max_calls=2, window_seconds=10.0)
    monkeypatch.setattr(limiter, "_MAX_KEYS", 3)
    now = [0.0]
    monkeypatch.setattr(api.time, "monotonic", lambda: now[0])

    for i in range(3):
        assert limiter.check(f"ip-{i}") is True
    now[0] = 100.0  # 全部命中已出窗口
    assert limiter.check("fresh-ip") is True  # 超上限触发清理
    assert set(limiter._hits) == {"fresh-ip"}


def test_rate_limiter_enforces_key_cap_for_live_clients(monkeypatch) -> None:
    limiter = api._RateLimiter(max_calls=5, window_seconds=60.0)
    monkeypatch.setattr(limiter, "_MAX_KEYS", 2)
    monkeypatch.setattr(api.time, "monotonic", lambda: 1.0)

    assert limiter.check("first") is True
    assert limiter.check("second") is True
    assert limiter.check("first") is True  # refresh; second is now least recently used
    assert limiter.check("third") is True

    assert list(limiter._hits) == ["first", "third"]


def _fake_request(headers: dict[str, str] | None = None, host: str = "10.0.0.9"):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=host))


def test_rate_limit_key_ignores_forwarded_header_by_default(monkeypatch) -> None:
    monkeypatch.delenv("APP_TRUST_PROXY", raising=False)
    req = _fake_request({"x-forwarded-for": "1.2.3.4"})
    assert api._rate_limit_key(req) == "10.0.0.9"  # 默认不信任可伪造的头


def test_rate_limit_key_uses_proxy_overwritten_client_ip_when_trusted(monkeypatch) -> None:
    monkeypatch.setenv("APP_TRUST_PROXY", "1")
    req = _fake_request(
        {
            "x-real-ip": "1.2.3.4",
            "x-forwarded-for": "spoofed, 10.0.0.1",
        }
    )
    assert api._rate_limit_key(req) == "1.2.3.4"
    forwarded_only = _fake_request({"x-forwarded-for": "2.3.4.5, 10.0.0.1"})
    assert api._rate_limit_key(forwarded_only) == "2.3.4.5"
    assert api._rate_limit_key(_fake_request({})) == "10.0.0.9"  # 无头回退直连 IP


def test_run_admission_rejects_when_active_and_queue_are_full() -> None:
    admission = api.RunAdmission(max_active_runs=1, max_queued_runs=0)
    active = admission.acquire()
    with pytest.raises(api.RunAdmissionLimit):
        admission.acquire()
    active.release()
    assert admission.active == 0


@pytest.mark.asyncio
async def test_run_admission_queued_lease_activates_and_releases() -> None:
    admission = api.RunAdmission(max_active_runs=1, max_queued_runs=1)
    active = admission.acquire()
    queued = admission.acquire()
    assert admission.active == 1
    assert admission.queued == 1

    waiting = asyncio.create_task(queued.wait())
    await asyncio.sleep(0)
    active.release()
    await waiting
    assert admission.active == 1
    assert admission.queued == 0
    queued.release()
    assert admission.active == 0


@pytest.mark.asyncio
async def test_run_admission_cancelled_waiter_returns_queue_capacity() -> None:
    admission = api.RunAdmission(max_active_runs=1, max_queued_runs=1)
    active = admission.acquire()
    queued = admission.acquire()
    waiting = asyncio.create_task(queued.wait())
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert admission.queued == 0
    active.release()
    assert admission.active == 0


@pytest.mark.asyncio
async def test_queued_run_renews_durable_lease_until_admitted(monkeypatch) -> None:
    admission = api.RunAdmission(max_active_runs=1, max_queued_runs=1)
    blocker = admission.acquire()
    queued = admission.acquire()
    renewals: list[tuple[str, str]] = []
    renewed_once = asyncio.Event()
    executed = asyncio.Event()

    class Repo:
        async def renew_lease(self, run_id, owner):  # type: ignore[no-untyped-def]
            renewals.append((run_id, owner))
            renewed_once.set()
            return True

    app = SimpleNamespace(state=SimpleNamespace(repo=Repo()))

    async def fake_execute(*args, **kwargs):  # type: ignore[no-untyped-def]
        executed.set()

    monkeypatch.setattr(api, "_execute", fake_execute)
    monkeypatch.setattr(api, "_LEASE_RENEW_INTERVAL_SECONDS", 0.001)
    task = asyncio.create_task(
        api._execute_with_admission(queued, app, "run-1", "query", Settings(), None, None, "owner")
    )
    await asyncio.wait_for(renewed_once.wait(), timeout=1)
    assert renewals and set(renewals) == {("run-1", "owner")}
    blocker.release()
    await asyncio.wait_for(task, timeout=1)
    assert executed.is_set()
    assert admission.active == 0


@pytest.mark.asyncio
async def test_queued_run_losing_lease_drops_local_live_state(monkeypatch) -> None:
    admission = api.RunAdmission(max_active_runs=1, max_queued_runs=1)
    blocker = admission.acquire()
    queued = admission.acquire()
    run_id = "lost-while-queued"
    hub = EventHub()

    class Repo:
        async def renew_lease(self, run_id, owner):  # type: ignore[no-untyped-def]
            return False

    app = SimpleNamespace(state=SimpleNamespace(repo=Repo(), live={run_id: hub}))
    executed = False

    async def fake_execute(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal executed
        executed = True

    monkeypatch.setattr(api, "_execute", fake_execute)
    monkeypatch.setattr(api, "_LEASE_RENEW_INTERVAL_SECONDS", 0.001)
    await api._execute_with_admission(
        queued, app, run_id, "query", Settings(), None, None, "old-owner"
    )

    assert executed is False
    assert run_id not in app.state.live
    assert admission.queued == 0
    blocker.release()
    assert admission.active == 0


@pytest.mark.asyncio
async def test_remotely_cancelled_queued_run_settles_before_execution(monkeypatch) -> None:
    repo = InMemoryRepository()
    owner = "queued-owner"
    run_id = await repo.create_run(
        "remote cancellation",
        execution=_recoverable_execution("remote cancellation"),
        lease_owner=owner,
    )
    assert await repo.request_cancel(run_id) == "cancelling"
    admission = api.RunAdmission(max_active_runs=1, max_queued_runs=1)
    blocker = admission.acquire()
    queued = admission.acquire()
    hub = EventHub()
    app = SimpleNamespace(state=SimpleNamespace(repo=repo, live={run_id: hub}))
    executed = False

    async def fake_execute(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal executed
        executed = True

    monkeypatch.setattr(api, "_execute", fake_execute)
    monkeypatch.setattr(api, "_CANCEL_POLL_SECONDS", 0.001)
    await asyncio.wait_for(
        api._execute_with_admission(
            queued, app, run_id, "remote cancellation", Settings(), None, None, owner
        ),
        timeout=1,
    )

    assert executed is False
    assert await repo.get_run_status(run_id) == "cancelled"
    assert await repo.acquire_lease(run_id, "replacement-worker") is True
    assert run_id not in app.state.live
    assert admission.queued == 0
    blocker.release()
    assert admission.active == 0
    events = [event async for event in hub.stream()]
    assert [event.type for event in events] == ["cancelled"]


@pytest.mark.asyncio
async def test_admitted_run_rechecks_durable_lease_before_execution(monkeypatch) -> None:
    admission = api.RunAdmission(max_active_runs=1, max_queued_runs=0)
    slot = admission.acquire()
    run_id = "lost-at-admission"
    hub = EventHub()
    released: list[tuple[str, str]] = []

    class Repo:
        async def renew_lease(self, run_id, owner):  # type: ignore[no-untyped-def]
            return False

        async def get_run_status(self, run_id):  # type: ignore[no-untyped-def]
            return "running"

        async def get_run_attempt(self, run_id):  # type: ignore[no-untyped-def]
            return 1

        async def release_lease(self, run_id, owner):  # type: ignore[no-untyped-def]
            released.append((run_id, owner))

    app = SimpleNamespace(state=SimpleNamespace(repo=Repo(), live={run_id: hub}))
    executed = False

    async def fake_execute(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal executed
        executed = True

    monkeypatch.setattr(api, "_execute", fake_execute)
    await api._execute_with_admission(
        slot, app, run_id, "query", Settings(), None, None, "expired-owner"
    )

    assert executed is False
    assert released == [(run_id, "expired-owner")]
    assert run_id not in app.state.live
    assert admission.active == 0
    await _assert_hub_closed(hub)


def _recoverable_execution(query: str, *, checkpoint_scratch: dict | None = None):
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": query})
    runtime.save_checkpoint(
        {"query": query, "scratch": checkpoint_scratch or {}},
        {"name": "deep", "steps": [{"kind": "agent", "agent": "synthesizer"}]},
    )
    return execution


@pytest.mark.asyncio
async def test_orphan_recovery_isolates_failures(monkeypatch) -> None:
    class PartiallyBrokenRepository(InMemoryRepository):
        def __init__(self) -> None:
            super().__init__()
            self.broken_run_id = ""

        async def get_run(self, run_id: str):  # type: ignore[no-untyped-def]
            if run_id == self.broken_run_id:
                raise RuntimeError("corrupt run")
            return await super().get_run(run_id)

    repo = PartiallyBrokenRepository()
    good_run_id = await repo.create_run(
        "recoverable", execution=_recoverable_execution("recoverable")
    )
    repo.broken_run_id = await repo.create_run("broken")
    starts: list[str] = []

    async def fake_execute(app, run_id, *args, **kwargs):  # type: ignore[no-untyped-def]
        starts.append(run_id)

    monkeypatch.setattr(api, "_execute", fake_execute)
    app = SimpleNamespace(
        state=SimpleNamespace(repo=repo, live={}, tasks=set(), settings=Settings())
    )

    await api._recover_orphaned_runs(app, app.state.settings)
    await asyncio.gather(*app.state.tasks)

    assert starts == [good_run_id]


@pytest.mark.asyncio
async def test_orphan_recovery_leaves_legacy_active_run_without_workflow_untouched() -> None:
    repo = InMemoryRepository()
    run_id = await repo.create_run("legacy active")
    await repo.set_status(run_id, "running")
    app = SimpleNamespace(
        state=SimpleNamespace(repo=repo, live={}, tasks=set(), settings=Settings())
    )

    await api._recover_orphaned_runs(app, app.state.settings)

    assert await repo.get_run_status(run_id) == "running"


@pytest.mark.asyncio
async def test_lifespan_stops_recovery_before_snapshotting_workers(monkeypatch) -> None:
    worker_started = asyncio.Event()
    worker_stopped = asyncio.Event()
    recovery_started = asyncio.Event()

    class Engine:
        async def dispose(self) -> None:
            assert worker_stopped.is_set()

    async def noop_recovery(app, settings):  # type: ignore[no-untyped-def]
        return None

    async def recovery_loop(app):  # type: ignore[no-untyped-def]
        recovery_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:

            async def late_worker() -> None:
                worker_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    worker_stopped.set()

            worker = asyncio.create_task(late_worker())
            app.state.tasks.add(worker)
            worker.add_done_callback(app.state.tasks.discard)
            await worker_started.wait()
            raise

    async def noop_prepare(engine, database_url):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(api.runtime_config, "load_overrides", lambda: {})
    monkeypatch.setattr(api, "make_engine", lambda database_url: Engine())
    monkeypatch.setattr(api, "prepare_sqlite_schema", noop_prepare)
    monkeypatch.setattr(api, "make_sessionmaker", lambda engine: object())
    monkeypatch.setattr(api, "SqlRepository", lambda sessionmaker: object())
    monkeypatch.setattr(api, "CatalogRepository", lambda sessionmaker: object())
    monkeypatch.setattr(api, "_recover_orphaned_runs", noop_recovery)
    monkeypatch.setattr(api, "_recovery_loop", recovery_loop)
    test_app = SimpleNamespace(state=SimpleNamespace())

    async with api.lifespan(test_app):
        await recovery_started.wait()

    assert worker_stopped.is_set()


@pytest.mark.asyncio
async def test_lifespan_drains_cancelled_task_cleanup_before_engine_dispose(monkeypatch) -> None:
    lease_released = asyncio.Event()
    run_id = "prestart-shutdown"

    class Repo:
        async def get_run_status(self, run_id):  # type: ignore[no-untyped-def]
            return "pending"

        async def get_run_attempt(self, run_id):  # type: ignore[no-untyped-def]
            return 1

        async def release_lease(self, run_id, owner):  # type: ignore[no-untyped-def]
            await asyncio.sleep(0)
            lease_released.set()

    class Engine:
        async def dispose(self) -> None:
            assert lease_released.is_set()

    async def noop_recovery(app, settings):  # type: ignore[no-untyped-def]
        return None

    async def noop_prepare(engine, database_url):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(api.runtime_config, "load_overrides", lambda: {})
    monkeypatch.setattr(api, "make_engine", lambda database_url: Engine())
    monkeypatch.setattr(api, "prepare_sqlite_schema", noop_prepare)
    monkeypatch.setattr(api, "make_sessionmaker", lambda engine: object())
    monkeypatch.setattr(api, "SqlRepository", lambda sessionmaker: Repo())
    monkeypatch.setattr(api, "CatalogRepository", lambda sessionmaker: object())
    monkeypatch.setattr(api, "_recover_orphaned_runs", noop_recovery)
    test_app = SimpleNamespace(state=SimpleNamespace())
    hub = EventHub()

    async with api.lifespan(test_app):
        test_app.state.live[run_id] = hub
        worker = asyncio.create_task(asyncio.Event().wait())
        api._track_run_task(test_app, run_id, worker, lease_owner="owner")

    assert lease_released.is_set()
    assert run_id not in test_app.state.live
    await _assert_hub_closed(hub)


@pytest.mark.asyncio
async def test_lifespan_disposes_engine_when_startup_is_cancelled(monkeypatch) -> None:
    prepare_started = asyncio.Event()
    disposed = asyncio.Event()

    class Engine:
        async def dispose(self) -> None:
            disposed.set()

    async def blocking_prepare(engine, database_url):  # type: ignore[no-untyped-def]
        prepare_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(api.runtime_config, "load_overrides", lambda: {})
    monkeypatch.setattr(api, "make_engine", lambda database_url: Engine())
    monkeypatch.setattr(api, "prepare_sqlite_schema", blocking_prepare)
    test_app = SimpleNamespace(state=SimpleNamespace())

    async def start() -> None:
        async with api.lifespan(test_app):
            raise AssertionError("cancelled startup must not yield")

    task = asyncio.create_task(start())
    await prepare_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert disposed.is_set()


@pytest.mark.asyncio
async def test_orphan_recovery_pages_through_all_candidates(monkeypatch) -> None:
    repo = InMemoryRepository()
    run_ids = [
        await repo.create_run(query, execution=_recoverable_execution(query))
        for query in ("one", "two", "three")
    ]
    starts: list[str] = []

    async def fake_execute(app, run_id, *args, **kwargs):  # type: ignore[no-untyped-def]
        starts.append(run_id)

    monkeypatch.setattr(api, "_RECOVERY_PAGE_SIZE", 2)
    monkeypatch.setattr(api, "_execute", fake_execute)
    app = SimpleNamespace(
        state=SimpleNamespace(repo=repo, live={}, tasks=set(), settings=Settings())
    )

    await api._recover_orphaned_runs(app, app.state.settings)
    await asyncio.gather(*app.state.tasks)

    assert set(starts) == set(run_ids)


@pytest.mark.asyncio
async def test_cancelling_recovery_pages_and_closes_stale_hubs(monkeypatch) -> None:
    repo = InMemoryRepository()
    run_ids = [
        await repo.create_run(query, execution=_recoverable_execution(query))
        for query in ("cancel-one", "cancel-two", "cancel-three")
    ]
    for run_id in run_ids:
        assert await repo.request_cancel(run_id) == "cancelling"
    hubs = {run_id: EventHub() for run_id in run_ids}
    app = SimpleNamespace(
        state=SimpleNamespace(repo=repo, live=dict(hubs), tasks=set(), settings=Settings())
    )
    monkeypatch.setattr(api, "_RECOVERY_PAGE_SIZE", 2)

    await api._recover_orphaned_runs(app, app.state.settings)

    assert [await repo.get_run_status(run_id) for run_id in run_ids] == [
        "cancelled",
        "cancelled",
        "cancelled",
    ]
    assert app.state.live == {}
    for hub in hubs.values():
        await _assert_hub_closed(hub)


@pytest.mark.asyncio
async def test_cancelling_recovery_isolates_broken_runs() -> None:
    class PartiallyBrokenRepository(InMemoryRepository):
        def __init__(self) -> None:
            super().__init__()
            self.broken_run_id = ""

        async def get_run(self, run_id: str):  # type: ignore[no-untyped-def]
            if run_id == self.broken_run_id:
                raise RuntimeError("corrupt cancelling run")
            return await super().get_run(run_id)

    repo = PartiallyBrokenRepository()
    healthy_id = await repo.create_run(
        "healthy cancellation", execution=_recoverable_execution("healthy cancellation")
    )
    repo.broken_run_id = await repo.create_run(
        "broken cancellation", execution=_recoverable_execution("broken cancellation")
    )
    assert await repo.request_cancel(healthy_id) == "cancelling"
    assert await repo.request_cancel(repo.broken_run_id) == "cancelling"
    healthy_hub = EventHub()
    app = SimpleNamespace(
        state=SimpleNamespace(
            repo=repo,
            live={healthy_id: healthy_hub},
            tasks=set(),
            settings=Settings(),
        )
    )

    await api._recover_orphaned_runs(app, app.state.settings)

    assert await repo.get_run_status(healthy_id) == "cancelled"
    assert await repo.get_run_status(repo.broken_run_id) == "cancelling"
    assert healthy_id not in app.state.live
    await _assert_hub_closed(healthy_hub)


@pytest.mark.asyncio
async def test_orphan_recovery_reloads_checkpoint_after_acquiring_lease(monkeypatch) -> None:
    class UpdatingRepository(InMemoryRepository):
        def __init__(self, fresh_execution) -> None:  # type: ignore[no-untyped-def]
            super().__init__()
            self.fresh_execution = fresh_execution
            self.reads = 0

        async def get_run(self, run_id: str):  # type: ignore[no-untyped-def]
            detail = await super().get_run(run_id)
            self.reads += 1
            if self.reads >= 2 and detail is not None:
                detail.orchestration = self.fresh_execution.model_copy(deep=True)
            return detail

    stale = _recoverable_execution("Q", checkpoint_scratch={"revision": "stale"})
    fresh = _recoverable_execution("Q", checkpoint_scratch={"revision": "fresh"})
    repo = UpdatingRepository(fresh)
    run_id = await repo.create_run("Q", execution=stale)
    captured: list[str] = []

    async def fake_execute(app, run_id, query, settings, workflow, resume_execution, lease_owner):  # type: ignore[no-untyped-def]
        captured.append(resume_execution.checkpoint["scratch"]["revision"])

    monkeypatch.setattr(api, "_execute", fake_execute)
    app = SimpleNamespace(
        state=SimpleNamespace(repo=repo, live={}, tasks=set(), settings=Settings())
    )

    await api._recover_orphaned_runs(app, app.state.settings)
    await asyncio.gather(*app.state.tasks)

    assert captured == ["fresh"]
    assert run_id in app.state.live


@pytest.mark.asyncio
async def test_concurrent_resume_starts_only_one_execution(monkeypatch) -> None:
    class BarrierRepository(InMemoryRepository):
        def __init__(self) -> None:
            super().__init__()
            self.read_count = 0
            self.both_reading = asyncio.Event()

        async def get_run(self, run_id: str):  # type: ignore[no-untyped-def]
            self.read_count += 1
            if self.read_count <= 2:
                if self.read_count == 2:
                    self.both_reading.set()
                await self.both_reading.wait()
                await asyncio.sleep(0)
            return await super().get_run(run_id)

    repo = BarrierRepository()
    run_id = await repo.create_run("resume-race")
    await repo.save_orchestration(run_id, _recoverable_execution("resume-race"))
    api.app.state.settings = Settings()
    api.app.state.repo = repo
    api.app.state.live = {}
    api.app.state.tasks = set()
    release = asyncio.Event()
    starts = 0

    async def fake_execute(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal starts
        starts += 1
        await release.wait()

    monkeypatch.setattr(api, "_execute", fake_execute)
    async with _client() as client:
        responses = await asyncio.gather(
            client.post(f"/api/runs/{run_id}/resume"),
            client.post(f"/api/runs/{run_id}/resume"),
        )

    assert sorted(response.status_code for response in responses) == [202, 409]
    await asyncio.sleep(0)
    assert starts == 1
    release.set()
    await asyncio.gather(*list(api.app.state.tasks))
    api.app.state.live.clear()


@pytest.mark.asyncio
async def test_manual_resume_reloads_checkpoint_after_acquiring_lease(monkeypatch) -> None:
    class UpdatingRepository(InMemoryRepository):
        def __init__(self, fresh_execution) -> None:  # type: ignore[no-untyped-def]
            super().__init__()
            self.fresh_execution = fresh_execution
            self.reads = 0

        async def get_run(self, run_id: str):  # type: ignore[no-untyped-def]
            detail = await super().get_run(run_id)
            self.reads += 1
            if self.reads >= 2 and detail is not None:
                detail.orchestration = self.fresh_execution.model_copy(deep=True)
            return detail

    stale = _recoverable_execution("Q", checkpoint_scratch={"revision": "stale"})
    fresh = _recoverable_execution("Q", checkpoint_scratch={"revision": "fresh"})
    repo = UpdatingRepository(fresh)
    run_id = await repo.create_run("Q", execution=stale)
    api.app.state.settings = Settings()
    api.app.state.repo = repo
    api.app.state.live = {}
    api.app.state.tasks = set()
    captured: list[str] = []

    async def fake_execute(app, run_id, query, settings, workflow, resume_execution, lease_owner):  # type: ignore[no-untyped-def]
        captured.append(resume_execution.checkpoint["scratch"]["revision"])

    monkeypatch.setattr(api, "_execute", fake_execute)
    async with _client() as client:
        response = await client.post(f"/api/runs/{run_id}/resume")
    await asyncio.gather(*api.app.state.tasks)

    assert response.status_code == 202
    assert captured == ["fresh"]
    api.app.state.live.clear()


@pytest.mark.asyncio
async def test_resume_restores_original_run_settings(repo, monkeypatch) -> None:
    run_id = await repo.create_run("settings")
    original = Settings(
        max_rounds=1,
        max_concurrency=2,
        max_tokens=321,
        require_corroboration=True,
    )
    execution = _recoverable_execution(
        "settings",
        checkpoint_scratch={
            RUN_SETTINGS_CHECKPOINT_KEY: {
                "max_rounds": original.max_rounds,
                "max_concurrency": original.max_concurrency,
                "max_tokens": original.max_tokens,
                "require_corroboration": original.require_corroboration,
            }
        },
    )
    await repo.save_orchestration(run_id, execution)
    api.app.state.settings = Settings(
        max_rounds=5,
        max_concurrency=9,
        max_tokens=999,
        require_corroboration=False,
    )
    captured: list[Settings] = []

    async def fake_execute(
        app, run_id, query, settings, workflow=None, resume_execution=None, lease_owner=None
    ):  # type: ignore[no-untyped-def]
        captured.append(settings)

    monkeypatch.setattr(api, "_execute", fake_execute)
    async with _client() as client:
        response = await client.post(f"/api/runs/{run_id}/resume")
    await asyncio.gather(*list(api.app.state.tasks))

    assert response.status_code == 202
    assert captured[0].max_rounds == 1
    assert captured[0].max_concurrency == 2
    assert captured[0].max_tokens == 321
    assert captured[0].require_corroboration is True
    detail = await repo.get_run(run_id)
    assert detail is not None and detail.status == "running"
    api.app.state.live.clear()


@pytest.mark.asyncio
async def test_execute_cleanup_survives_lease_release_failure(monkeypatch) -> None:
    run_id = "cleanup"
    hub = EventHub()
    agent_closed = False
    search_closed = False

    class Repo:
        async def acquire_lease(self, run_id, owner):  # type: ignore[no-untyped-def]
            return True

        async def release_lease(self, run_id, owner):  # type: ignore[no-untyped-def]
            raise RuntimeError("release failed")

    class Search:
        async def aclose(self) -> None:
            nonlocal search_closed
            search_closed = True

    class Agent:
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.tracer = Tracer()

        async def run(self, query):  # type: ignore[no-untyped-def]
            return None

        async def aclose(self) -> None:
            nonlocal agent_closed
            agent_closed = True

    app = SimpleNamespace(state=SimpleNamespace(repo=Repo(), catalog=object(), live={run_id: hub}))
    monkeypatch.setattr(execution_module, "DeepResearchAgent", Agent)
    monkeypatch.setattr(
        execution_module.RunExecutor,
        "build_search_tool",
        lambda self, settings: asyncio.sleep(0, Search()),
    )

    await api._execute(app, run_id, "Q", Settings(), lease_owner="lease")

    assert agent_closed is True
    assert search_closed is True
    assert run_id not in app.state.live
    assert [event async for event in hub.stream()] == []


@pytest.mark.asyncio
async def test_execute_persists_user_requested_cancellation(monkeypatch) -> None:
    repo = InMemoryRepository()
    execution = _recoverable_execution("cancel")
    owner = "cancel-owner"
    run_id = await repo.create_run("cancel", execution=execution, lease_owner=owner)
    await repo.set_status(run_id, "running", lease_owner=owner)
    hub = EventHub()
    started = asyncio.Event()

    class Agent:
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.tracer = Tracer()

        async def run(self, query):  # type: ignore[no-untyped-def]
            started.set()
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            return None

    app = SimpleNamespace(state=SimpleNamespace(repo=repo, catalog=object(), live={run_id: hub}))
    monkeypatch.setattr(execution_module, "DeepResearchAgent", Agent)
    monkeypatch.setattr(
        execution_module.RunExecutor,
        "build_search_tool",
        lambda self, settings: asyncio.sleep(0, None),
    )

    task = asyncio.create_task(api._execute(app, run_id, "cancel", Settings(), lease_owner=owner))
    await started.wait()
    assert await repo.request_cancel(run_id) == "cancelling"
    task.cancel()
    await task

    assert await repo.get_run_status(run_id) == "cancelled"
    events = await repo.get_events(run_id)
    assert events[-1].type == "cancelled"
    assert events[-1].data == {"status": "cancelled"}


@pytest.mark.asyncio
async def test_resume_does_not_replay_previous_terminal_event(monkeypatch) -> None:
    owner = "resume-owner"
    execution = _recoverable_execution("resume-history")
    repo = InMemoryRepository()
    run_id = await repo.create_run("resume-history", execution=execution, lease_owner=owner)
    await repo.save_events(
        run_id,
        [
            Event(stage="PLANNER", type="info", message="old progress"),
            Event(stage="ORCHESTRATOR", type="error", message="old failure"),
        ],
        lease_owner=owner,
    )
    hub = EventHub()

    class Agent:
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.tracer = Tracer()

        async def run(self, query):  # type: ignore[no-untyped-def]
            self.tracer.emit("ORCHESTRATOR", "done", "new success")

        async def aclose(self) -> None:
            return None

    app = SimpleNamespace(state=SimpleNamespace(repo=repo, catalog=object(), live={run_id: hub}))
    monkeypatch.setattr(execution_module, "DeepResearchAgent", Agent)
    monkeypatch.setattr(
        execution_module.RunExecutor,
        "build_search_tool",
        lambda self, settings: asyncio.sleep(0, None),
    )

    await api._execute(
        app,
        run_id,
        "resume-history",
        Settings(),
        resume_execution=execution,
        lease_owner=owner,
    )

    events = [event async for event in hub.stream()]
    assert [(event.type, event.message) for event in events] == [
        ("info", "old progress"),
        ("done", "new success"),
    ]


@pytest.mark.asyncio
async def test_lease_renewal_failure_cancels_execution(monkeypatch) -> None:
    run_id = "lost-lease"
    hub = EventHub()
    statuses: list[str] = []

    class Repo:
        async def renew_lease(self, run_id, owner):  # type: ignore[no-untyped-def]
            return False

        async def release_lease(self, run_id, owner):  # type: ignore[no-untyped-def]
            return None

        async def set_status(self, run_id, status, *, lease_owner=None):  # type: ignore[no-untyped-def]
            statuses.append(status)

    class Agent:
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.tracer = Tracer()

        async def run(self, query):  # type: ignore[no-untyped-def]
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            return None

    app = SimpleNamespace(state=SimpleNamespace(repo=Repo(), catalog=object(), live={run_id: hub}))
    monkeypatch.setattr(api, "_LEASE_RENEW_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(execution_module, "DeepResearchAgent", Agent)
    monkeypatch.setattr(
        execution_module.RunExecutor,
        "build_search_tool",
        lambda self, settings: asyncio.sleep(0, None),
    )

    with pytest.raises(asyncio.CancelledError):
        await api._execute(app, run_id, "Q", Settings(), lease_owner="lease")

    events = [event async for event in hub.stream()]
    assert statuses == []
    assert any(event.type == "error" and "租约" in event.message for event in events)
    assert run_id not in app.state.live


@pytest.mark.asyncio
async def test_execute_cleanup_survives_second_cancellation(monkeypatch) -> None:
    run_id = "double-cancel"
    hub = EventHub()
    run_started = asyncio.Event()
    close_started = asyncio.Event()
    allow_close = asyncio.Event()

    class Repo:
        pass

    class Agent:
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.tracer = Tracer()
            self.closed = False

        async def run(self, query):  # type: ignore[no-untyped-def]
            run_started.set()
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            close_started.set()
            await allow_close.wait()
            self.closed = True

    class Search:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    search = Search()
    app = SimpleNamespace(state=SimpleNamespace(repo=Repo(), catalog=object(), live={run_id: hub}))
    monkeypatch.setattr(execution_module, "DeepResearchAgent", Agent)

    async def build_search(self, settings):  # type: ignore[no-untyped-def]
        return search

    monkeypatch.setattr(execution_module.RunExecutor, "build_search_tool", build_search)
    task = asyncio.create_task(api._execute(app, run_id, "Q", Settings()))
    await run_started.wait()
    task.cancel()
    await close_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    allow_close.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert search.closed is True
    assert run_id not in app.state.live
    assert [event async for event in hub.stream()] == []


@pytest.mark.asyncio
async def test_cancelled_execution_remains_recoverable(monkeypatch) -> None:
    repo = InMemoryRepository()
    execution = _recoverable_execution("restartable")
    owner = "shutting-down-worker"
    run_id = await repo.create_run("restartable", execution=execution, lease_owner=owner)
    await repo.set_status(run_id, "running", lease_owner=owner)
    hub = EventHub()
    started = asyncio.Event()

    class Agent:
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.tracer = Tracer()

        async def run(self, query):  # type: ignore[no-untyped-def]
            started.set()
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            return None

    first_app = SimpleNamespace(
        state=SimpleNamespace(repo=repo, catalog=object(), live={run_id: hub})
    )
    monkeypatch.setattr(execution_module, "DeepResearchAgent", Agent)
    monkeypatch.setattr(
        execution_module.RunExecutor,
        "build_search_tool",
        lambda self, settings: asyncio.sleep(0, None),
    )

    task = asyncio.create_task(
        api._execute(
            first_app,
            run_id,
            "restartable",
            Settings(),
            resume_execution=execution,
            lease_owner=owner,
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    interrupted = await repo.get_run(run_id)
    assert interrupted is not None
    assert interrupted.status == "running"

    resumed: list[str] = []

    async def fake_execute(app, run_id, *args, **kwargs):  # type: ignore[no-untyped-def]
        resumed.append(run_id)

    monkeypatch.setattr(api, "_execute", fake_execute)
    second_app = SimpleNamespace(
        state=SimpleNamespace(repo=repo, live={}, tasks=set(), settings=Settings())
    )

    await api._recover_orphaned_runs(second_app, second_app.state.settings)
    await asyncio.gather(*second_app.state.tasks)

    assert resumed == [run_id]


@pytest.mark.asyncio
async def test_report_document_endpoint_delivers_the_evidence_apparatus(repo):
    """结构化文档端点：证据装置随报告一起交付，不再只存在于前端的即时 join。

    这是 Markdown / HTML / 打印三种导出的共同数据源，所以它必须自带引用、
    证据附录与概览——否则每个导出出口都要自己再 join 一遍。
    """
    from deep_research.models import EvidenceVerification, Finding, ResearchResult

    run_id = await repo.create_run("CASSI 重建方法对比")
    url = "https://doi.org/10.1364/oe.1"
    finding = Finding(
        statement="该方法达到 38.36 dB",
        source_url=url,
        evidence_quote="38.36 dB",
        verification=EvidenceVerification(
            status="verified",
            method="normalized_quote",
            source_content_hash="ab" * 32,
            source_reference="Cai 等. DAUHST. NeurIPS, 2022. " + url,
            evidence_context="Our method achieves 38.36 dB on KAIST.",
            reason="quote_found_in_source",
            semantic_status="supported",
            semantic_confidence=0.9,
            claim_id="C-1",
        ),
    )
    await repo.save_result(run_id, ResearchResult(sub_question="精度如何", findings=[finding]))
    await repo.save_report(
        run_id,
        Report(
            query="CASSI 重建方法对比",
            markdown="正文引用 [1]。\n\n## 参考来源\n[1] " + url + "\n",
            citations=[url],
        ),
    )
    await repo.append_events(
        run_id,
        [Event(stage="RESEARCHER", type="info", data={"category": "source_policy", "blocked": 2})],
    )

    async with _client() as c:
        resp = await c.get(f"/api/runs/{run_id}/document")

    assert resp.status_code == 200
    doc = resp.json()
    # 正文里 Synthesizer 追加的参考来源段落已剥掉，由结构化字段承载
    assert doc["blocks"][0]["markdown"] == "正文引用 [1]。"
    assert doc["references"] == [
        {"index": 1, "url": url, "reference": "Cai 等. DAUHST. NeurIPS, 2022. " + url}
    ]
    assert doc["evidence"][0]["citation"] == 1
    assert doc["evidence"][0]["content_hash"] == "ab" * 32
    assert doc["overview"]["blocked_sources"] == 2
    assert "不保证论断在开放世界为真" in doc["disclaimer"]


@pytest.mark.asyncio
async def test_report_document_endpoint_404s_for_an_unknown_run(repo):
    async with _client() as c:
        resp = await c.get("/api/runs/does-not-exist/document")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_report_document_endpoint_works_before_a_report_exists(repo):
    """流式中途/失败的 run 也要能取文档，否则打印预览在这些状态下会崩。"""
    run_id = await repo.create_run("尚未产出报告")

    async with _client() as c:
        resp = await c.get(f"/api/runs/{run_id}/document")

    assert resp.status_code == 200
    doc = resp.json()
    assert doc["blocks"] == []
    assert doc["references"] == []
    assert doc["overview"]["blocked_sources"] is None


@pytest.mark.asyncio
async def test_report_markdown_endpoint_carries_the_evidence_apparatus(repo):
    """.md 下载与 report.markdown 不是同一份字节。

    前者是结构化文档的投影，带逐字引文、验证状态与快照哈希；后者只有综合者写的
    正文。这个端点存在的全部理由就是这个差别，所以断言必须落在差别上，而不是
    "返回了 200"。
    """
    from deep_research.models import EvidenceVerification, Finding, ResearchResult

    run_id = await repo.create_run("CASSI 重建方法对比")
    url = "https://doi.org/10.1364/oe.2"
    finding = Finding(
        statement="该方法达到 38.36 dB",
        source_url=url,
        evidence_quote="38.36 dB",
        verification=EvidenceVerification(
            status="verified",
            method="normalized_quote",
            source_content_hash="cd" * 32,
            source_reference="Cai 等. DAUHST. NeurIPS, 2022. " + url,
            evidence_context="Our method achieves 38.36 dB on KAIST.",
            reason="quote_found_in_source",
            semantic_status="supported",
            semantic_confidence=0.9,
            claim_id="C-1",
        ),
    )
    await repo.save_result(run_id, ResearchResult(sub_question="精度如何", findings=[finding]))
    await repo.save_report(
        run_id,
        Report(
            query="CASSI 重建方法对比",
            markdown="正文引用 [1]。\n\n## 参考来源\n[1] " + url + "\n",
            citations=[url],
        ),
    )

    async with _client() as c:
        resp = await c.get(f"/api/runs/{run_id}/document.md")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert resp.headers["content-disposition"].endswith(f'research-{run_id}.md"')
    body = resp.text
    # 正文在
    assert "正文引用 [1]" in body
    # 证据装置也在——这几项在 report.markdown 里一个都没有
    assert "cd" * 32 in body
    assert "38.36 dB" in body
    assert "Cai 等. DAUHST. NeurIPS, 2022." in body
    assert "不保证论断在开放世界为真" in body


@pytest.mark.asyncio
async def test_report_markdown_endpoint_works_before_a_report_exists(repo):
    """流式中途的 run 也要能导出，与 /document、.csv 的降级口径一致。"""
    run_id = await repo.create_run("尚未产出报告")

    async with _client() as c:
        resp = await c.get(f"/api/runs/{run_id}/document.md")

    assert resp.status_code == 200
    assert "不保证论断在开放世界为真" in resp.text


@pytest.mark.asyncio
async def test_report_markdown_endpoint_404s_for_an_unknown_run(repo):
    async with _client() as c:
        resp = await c.get("/api/runs/does-not-exist/document.md")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_report_csv_endpoint_returns_an_empty_download_before_a_table_exists(repo):
    run_id = await repo.create_run("尚未产出表格")

    async with _client() as c:
        resp = await c.get(f"/api/runs/{run_id}/document.csv")

    assert resp.status_code == 200
    assert resp.text == ""
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"].endswith(f'research-{run_id}.csv"')


@pytest.mark.asyncio
async def test_report_csv_endpoint_404s_for_an_unknown_run(repo):
    async with _client() as c:
        resp = await c.get("/api/runs/does-not-exist/document.csv")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_report_xlsx_endpoint_returns_an_openable_download_before_a_table_exists(repo):
    """The optional exporter still returns a valid workbook for a streaming run."""
    run_id = await repo.create_run("尚未产出表格")

    async with _client() as c:
        resp = await c.get(f"/api/runs/{run_id}/document.xlsx")

    assert resp.status_code == 200
    assert resp.content[:4] == b"PK\x03\x04"
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert resp.headers["content-disposition"].endswith(f'research-{run_id}.xlsx"')


@pytest.mark.asyncio
async def test_report_xlsx_endpoint_404s_for_an_unknown_run(repo):
    async with _client() as c:
        resp = await c.get("/api/runs/does-not-exist/document.xlsx")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_report_xlsx_endpoint_returns_501_when_optional_dependency_is_missing(
    repo, monkeypatch
):
    run_id = await repo.create_run("需要可选依赖")

    def _missing_dependency(*args, **kwargs):
        raise api.XlsxDependencyError("install the xlsx extra")

    monkeypatch.setattr(api, "render_xlsx", _missing_dependency)
    async with _client() as c:
        resp = await c.get(f"/api/runs/{run_id}/document.xlsx")

    assert resp.status_code == 501
    assert "xlsx extra" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_report_pdf_endpoint_returns_optional_dependency_status(repo, monkeypatch):
    run_id = await repo.create_run("server pdf optional")
    import sys

    monkeypatch.setitem(sys.modules, "weasyprint", None)
    async with _client() as c:
        resp = await c.get(f"/api/runs/{run_id}/document.pdf")
    assert resp.status_code == 501
    assert "optional 'pdf' extra" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_report_pdf_endpoint_404s_for_an_unknown_run(repo):
    async with _client() as c:
        resp = await c.get("/api/runs/does-not-exist/document.pdf")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_report_export_endpoints_forward_hsi_and_table_selection(repo, monkeypatch):
    """All document exports must assemble the same opt-in HSI document."""
    from deep_research.report import ReportDocument, TableBlock

    run_id = await repo.create_run("HSI export parameters")
    document = ReportDocument(
        query="HSI export parameters",
        blocks=[TableBlock(id="hsi_reconstruction", columns=[], rows=[])],
    )
    assembled: list[dict[str, object]] = []

    def fake_assemble(*args, **kwargs):  # type: ignore[no-untyped-def]
        assembled.append(kwargs)
        return document

    monkeypatch.setattr(api, "assemble_document", fake_assemble)
    csv_calls: list[str | None] = []
    xlsx_calls: list[str | None] = []
    pdf_calls: list[ReportDocument] = []

    def fake_csv(value, *, table_id=None):  # type: ignore[no-untyped-def]
        assert value is document
        csv_calls.append(table_id)
        return "object\r\n"

    def fake_xlsx(value, *, table_id=None):  # type: ignore[no-untyped-def]
        assert value is document
        xlsx_calls.append(table_id)
        return b"xlsx"

    def fake_pdf(value):  # type: ignore[no-untyped-def]
        pdf_calls.append(value)
        return b"%PDF"

    monkeypatch.setattr(api, "render_csv", fake_csv)
    monkeypatch.setattr(api, "render_xlsx", fake_xlsx)
    monkeypatch.setattr(api, "render_pdf", fake_pdf)

    async with _client() as c:
        document_response = await c.get(f"/api/runs/{run_id}/document?include_hsi_tables=true")
        csv_response = await c.get(
            f"/api/runs/{run_id}/document.csv?include_hsi_tables=true&table_id=hsi_reconstruction"
        )
        xlsx_response = await c.get(
            f"/api/runs/{run_id}/document.xlsx?include_hsi_tables=true&table_id=hsi_reconstruction"
        )
        pdf_response = await c.get(f"/api/runs/{run_id}/document.pdf?include_hsi_tables=true")

    assert document_response.status_code == 200
    assert csv_response.status_code == 200
    assert xlsx_response.status_code == 200
    assert pdf_response.status_code == 200
    assert [kwargs["include_hsi_tables"] for kwargs in assembled] == [True] * 4
    assert csv_calls == ["hsi_reconstruction"]
    assert xlsx_calls == ["hsi_reconstruction"]
    assert pdf_calls == [document]


@pytest.mark.asyncio
async def test_report_exports_forward_persisted_corroboration_gate(repo, monkeypatch):
    """Strict runs must keep the corroboration gate in every export path."""
    from deep_research.report import ReportDocument

    execution = OrchestrationRuntime().start("deep", {"query": "strict export"})
    execution.checkpoint = {
        "scratch": {
            RUN_SETTINGS_CHECKPOINT_KEY: {"require_corroboration": True},
        }
    }
    run_id = await repo.create_run("strict export", execution=execution)
    assembled: list[dict[str, object]] = []

    def fake_assemble(*args, **kwargs):  # type: ignore[no-untyped-def]
        assembled.append(kwargs)
        return ReportDocument(query="strict export")

    monkeypatch.setattr(api, "assemble_document", fake_assemble)
    monkeypatch.setattr(api, "render_csv", lambda *args, **kwargs: "")
    monkeypatch.setattr(api, "render_xlsx", lambda *args, **kwargs: b"xlsx")
    monkeypatch.setattr(api, "render_pdf", lambda *args, **kwargs: b"%PDF")

    async with _client() as c:
        responses = await asyncio.gather(
            c.get(f"/api/runs/{run_id}/document"),
            c.get(f"/api/runs/{run_id}/document.csv"),
            c.get(f"/api/runs/{run_id}/document.xlsx"),
            c.get(f"/api/runs/{run_id}/document.pdf"),
        )

    assert all(response.status_code == 200 for response in responses)
    assert len(assembled) == 4
    assert all(kwargs["require_corroboration"] is True for kwargs in assembled)


@pytest.mark.asyncio
async def test_report_table_export_maps_selection_errors(repo, monkeypatch):
    from deep_research.report import (
        CsvTableNotFoundError,
        CsvTableSelectionError,
        XlsxTableNotFoundError,
        XlsxTableSelectionError,
    )

    run_id = await repo.create_run("invalid table selection")

    async with _client() as c:
        monkeypatch.setattr(
            api,
            "render_csv",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                CsvTableSelectionError("table_id is required")
            ),
        )
        csv_ambiguous = await c.get(f"/api/runs/{run_id}/document.csv")

        monkeypatch.setattr(
            api,
            "render_csv",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                CsvTableNotFoundError("table not found: missing")
            ),
        )
        csv_missing = await c.get(f"/api/runs/{run_id}/document.csv?table_id=missing")

        monkeypatch.setattr(
            api,
            "render_xlsx",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                XlsxTableSelectionError("table_id is required")
            ),
        )
        xlsx_ambiguous = await c.get(f"/api/runs/{run_id}/document.xlsx")

        monkeypatch.setattr(
            api,
            "render_xlsx",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                XlsxTableNotFoundError("table not found: missing")
            ),
        )
        xlsx_missing = await c.get(f"/api/runs/{run_id}/document.xlsx?table_id=missing")

    assert csv_ambiguous.status_code == 400
    assert csv_missing.status_code == 404
    assert xlsx_ambiguous.status_code == 400
    assert xlsx_missing.status_code == 404
