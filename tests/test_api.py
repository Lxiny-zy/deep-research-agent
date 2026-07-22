"""API 端点测试：httpx ASGITransport + 注入 InMemoryRepository，后台执行被 monkeypatch。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport

from deep_research import api
from deep_research.config import Settings
from deep_research.models import Report, ResearchPlan, SubQuestion
from deep_research.observability import Event, EventHub, Tracer
from deep_research.orchestration import OrchestrationRuntime
from deep_research.orchestrator import RUN_SETTINGS_CHECKPOINT_KEY
from deep_research.persistence.memory_repository import InMemoryRepository


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test")


@pytest.fixture
def repo(monkeypatch) -> InMemoryRepository:
    r = InMemoryRepository()
    api.app.state.settings = Settings()  # lifespan 未在 ASGITransport 下触发，手动注入
    api.app.state.repo = r
    api.app.state.live = {}
    api.app.state.tasks = set()

    async def _noop(
        app,
        run_id,
        query,
        settings,
        workflow=None,
        resume_execution=None,
        lease_owner=None,
        initial_execution=None,
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
    assert evs.json()[0]["stage"] == "PLANNER"
    assert missing.status_code == 404


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
async def test_remote_stream_waits_for_durable_terminal_events(repo, monkeypatch) -> None:
    run_id = await repo.create_run("remote")
    await repo.set_status(run_id, "running")
    await repo.save_events(
        run_id,
        [Event(stage="ORCHESTRATOR", type="error", message="old attempt")],
    )
    remote_app = SimpleNamespace(state=SimpleNamespace(repo=repo, live={}))
    monkeypatch.setattr(api, "_REMOTE_STREAM_POLL_SECONDS", 0.001)
    monkeypatch.setattr(api, "_REMOTE_STREAM_TERMINAL_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(api, "_SSE_HEARTBEAT_SECONDS", 60.0)

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
    monkeypatch.setattr(api, "_REMOTE_STREAM_POLL_SECONDS", 0.001)
    monkeypatch.setattr(api, "_REMOTE_STREAM_TERMINAL_GRACE_SECONDS", 0.005)

    payload = "".join(
        [chunk async for chunk in api._stream_run_sse(remote_app, run_id)]
    )

    assert '"type":"error"' in payload
    assert '"status":"error"' in payload


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
    run_id = await repo.create_run(
        "retry failure", execution=execution, lease_owner=owner
    )
    await repo.set_status(run_id, "error", lease_owner=owner)
    await repo.save_events(
        run_id,
        [Event(stage="ORCHESTRATOR", type="error", message="old failure")],
        lease_owner=owner,
    )
    remote_app = SimpleNamespace(state=SimpleNamespace(repo=repo, live={}))
    monkeypatch.setattr(api, "_REMOTE_STREAM_POLL_SECONDS", 0.001)
    monkeypatch.setattr(api, "_REMOTE_STREAM_TERMINAL_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(api, "_SSE_HEARTBEAT_SECONDS", 60.0)

    async def consume() -> str:
        return "".join(
            [chunk async for chunk in api._stream_run_sse(remote_app, run_id)]
        )

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
async def test_legacy_research_uses_runtime_settings(repo, monkeypatch):
    runtime_settings = Settings(llm_model='saved-model', llm_api_key='saved-key')
    api.app.state.settings = runtime_settings
    captured = {}

    class FakeAgent:
        def __init__(self, settings):
            captured['settings'] = settings

        async def run_stream(self, query):
            assert query == 'Q'
            yield Event(stage='ORCHESTRATOR', type='done')

        async def aclose(self):
            return None

    monkeypatch.setattr(api, 'DeepResearchAgent', FakeAgent)
    monkeypatch.setattr(api, '_check_rate_limit', lambda request: None)

    async with _client() as c:
        resp = await c.get('/api/research', params={'q': 'Q'})

    assert resp.status_code == 200
    assert captured['settings'] is runtime_settings


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
    assert "sk-aaaa1234" not in resp.text


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
async def test_config_put_sets_and_persists_secret(repo, tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.json"
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(cfg))
    async with _client() as c:
        resp = await c.put("/api/config", json={"llm_api_key": "sk-new-1234"})
    assert resp.status_code == 200
    assert api.app.state.settings.llm_api_key == "sk-new-1234"
    assert resp.json()["llm_api_key_hint"] == "…1234"
    assert "sk-new-1234" not in resp.text  # 响应已脱敏
    assert json.loads(cfg.read_text(encoding="utf-8"))["llm_api_key"] == "sk-new-1234"


@pytest.mark.asyncio
async def test_config_put_validates_range(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(tmp_path / "cfg.json"))
    async with _client() as c:
        resp = await c.put("/api/config", json={"max_concurrency": 0})
    assert resp.status_code == 422


def _recoverable_execution(
    query: str, *, checkpoint_scratch: dict | None = None
):
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

    async def fake_execute(
        app, run_id, query, settings, workflow, resume_execution, lease_owner
    ):  # type: ignore[no-untyped-def]
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

    async def fake_execute(
        app, run_id, query, settings, workflow, resume_execution, lease_owner
    ):  # type: ignore[no-untyped-def]
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
    original = Settings(max_rounds=1, max_concurrency=2, max_tokens=321)
    execution = _recoverable_execution(
        "settings",
        checkpoint_scratch={
            RUN_SETTINGS_CHECKPOINT_KEY: {
                "max_rounds": original.max_rounds,
                "max_concurrency": original.max_concurrency,
                "max_tokens": original.max_tokens,
            }
        },
    )
    await repo.save_orchestration(run_id, execution)
    api.app.state.settings = Settings(max_rounds=5, max_concurrency=9, max_tokens=999)
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

    app = SimpleNamespace(
        state=SimpleNamespace(repo=Repo(), catalog=object(), live={run_id: hub})
    )
    monkeypatch.setattr(api, "DeepResearchAgent", Agent)
    monkeypatch.setattr(api, "_build_search_tool", lambda app, settings: asyncio.sleep(0, Search()))

    await api._execute(app, run_id, "Q", Settings(), lease_owner="lease")

    assert agent_closed is True
    assert search_closed is True
    assert run_id not in app.state.live
    assert [event async for event in hub.stream()] == []


@pytest.mark.asyncio
async def test_resume_does_not_replay_previous_terminal_event(monkeypatch) -> None:
    owner = "resume-owner"
    execution = _recoverable_execution("resume-history")
    repo = InMemoryRepository()
    run_id = await repo.create_run(
        "resume-history", execution=execution, lease_owner=owner
    )
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

    app = SimpleNamespace(
        state=SimpleNamespace(repo=repo, catalog=object(), live={run_id: hub})
    )
    monkeypatch.setattr(api, "DeepResearchAgent", Agent)
    monkeypatch.setattr(api, "_build_search_tool", lambda app, settings: asyncio.sleep(0, None))

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

        async def set_status(
            self, run_id, status, *, lease_owner=None
        ):  # type: ignore[no-untyped-def]
            statuses.append(status)

    class Agent:
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.tracer = Tracer()

        async def run(self, query):  # type: ignore[no-untyped-def]
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            return None

    app = SimpleNamespace(
        state=SimpleNamespace(repo=Repo(), catalog=object(), live={run_id: hub})
    )
    monkeypatch.setattr(api, "_LEASE_RENEW_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(api, "DeepResearchAgent", Agent)
    monkeypatch.setattr(api, "_build_search_tool", lambda app, settings: asyncio.sleep(0, None))

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
    app = SimpleNamespace(
        state=SimpleNamespace(repo=Repo(), catalog=object(), live={run_id: hub})
    )
    monkeypatch.setattr(api, "DeepResearchAgent", Agent)

    async def build_search(app, settings):  # type: ignore[no-untyped-def]
        return search

    monkeypatch.setattr(api, "_build_search_tool", build_search)
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
    run_id = await repo.create_run(
        "restartable", execution=execution, lease_owner=owner
    )
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
    monkeypatch.setattr(api, "DeepResearchAgent", Agent)
    monkeypatch.setattr(api, "_build_search_tool", lambda app, settings: asyncio.sleep(0, None))

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
