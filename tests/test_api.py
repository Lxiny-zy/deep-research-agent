"""API 端点测试：httpx ASGITransport + 注入 InMemoryRepository，后台执行被 monkeypatch。"""

from __future__ import annotations

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

    async def _noop(app, run_id, query, settings):  # 不跑真实 agent（避免触发 LLM/检索）
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
