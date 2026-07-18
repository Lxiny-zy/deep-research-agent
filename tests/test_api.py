"""API 端点测试：httpx ASGITransport + 注入 InMemoryRepository，后台执行被 monkeypatch。"""

from __future__ import annotations

import json

import httpx
import pytest
from httpx import ASGITransport

from deep_research import api
from deep_research.config import Settings
from deep_research.models import Report, ResearchPlan, SubQuestion
from deep_research.observability import Event
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

    async def _noop(app, run_id, query, settings, workflow=None):  # 不跑真实 agent
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
    async with _client() as c:
        resp = await c.get(f"/api/runs/{run_id}/stream")
    assert resp.status_code == 200
    assert "PLANNER" in resp.text
    assert "done" in resp.text


@pytest.mark.asyncio
async def test_delete_run(repo):
    run_id = await repo.create_run("待删")
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
async def test_batch_delete_skips_running(repo):
    a = await repo.create_run("A")
    b = await repo.create_run("B")
    c_id = await repo.create_run("C")
    api.app.state.live[c_id] = object()  # C 进行中，应跳过
    async with _client() as c:
        resp = await c.post("/api/runs/batch_delete", json={"ids": [a, b, c_id]})
    api.app.state.live.pop(c_id, None)
    body = resp.json()
    assert body == {"deleted": 2, "skipped": 1}
    assert await repo.get_run(a) is None
    assert await repo.get_run(c_id) is not None


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
