from datetime import UTC, datetime, timedelta

import httpx
import pytest

from deep_research import api
from deep_research.config import Settings
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.persistence.sql_repository import SqlRepository


@pytest.fixture(params=["memory", "sqlite"])
async def cancel_repo(request, tmp_path):
    if request.param == "memory":
        yield InMemoryRepository()
        return
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'cancel.db').as_posix()}")
    await create_all(engine)
    try:
        yield SqlRepository(make_sessionmaker(engine))
    finally:
        await engine.dispose()


async def test_cancel_timestamp_is_first_transition_time_and_survives_reload(cancel_repo):
    run_id = await cancel_repo.create_run("cancel time")
    assert (await cancel_repo.get_run(run_id)).cancel_requested_at is None
    before = datetime.now(UTC)
    assert await cancel_repo.request_cancel(run_id) == "cancelling"
    first = (await cancel_repo.get_run(run_id)).cancel_requested_at
    assert first is not None
    assert before <= first <= datetime.now(UTC)
    assert await cancel_repo.request_cancel(run_id) == "cancelling"
    assert (await cancel_repo.get_run(run_id)).cancel_requested_at == first
    await cancel_repo.set_status(run_id, "cancelled")
    assert await cancel_repo.request_cancel(run_id) == "cancelled"
    assert (await cancel_repo.get_run(run_id)).cancel_requested_at == first


@pytest.mark.parametrize("status", ["done", "error", "needs_review", "cancelled"])
async def test_cancel_does_not_timestamp_terminal_runs(cancel_repo, status):
    run_id = await cancel_repo.create_run("terminal")
    await cancel_repo.set_status(run_id, status)
    assert await cancel_repo.request_cancel(run_id) == status
    assert (await cancel_repo.get_run(run_id)).cancel_requested_at is None


@pytest.mark.parametrize("age, delayed", [(0, False), (119, False), (121, True)])
async def test_detail_reports_delayed_cancel_without_settling_it(monkeypatch, age, delayed):
    repo = InMemoryRepository()
    run_id = await repo.create_run("cancel")
    await repo.request_cancel(run_id)
    repo._runs[run_id].cancel_requested_at = datetime.now(UTC) - timedelta(seconds=age)
    monkeypatch.setattr(api.app.state, "repo", repo, raising=False)
    monkeypatch.setattr(api.app.state, "settings", Settings(execution_mode="worker"), raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["cancel_requested_at"] is not None
    assert bool(data["status_notice"]) is delayed
    if delayed:
        assert "取消请求已保存" in data["status_notice"]
        assert "执行服务" in data["status_notice"]
    assert await repo.get_run_status(run_id) == "cancelling"


@pytest.mark.parametrize("status", ["cancelling", "cancelled", "done"])
async def test_detail_does_not_invent_legacy_cancel_age(monkeypatch, status):
    repo = InMemoryRepository()
    run_id = await repo.create_run("legacy")
    await repo.set_status(run_id, status)
    monkeypatch.setattr(api.app.state, "repo", repo, raising=False)
    monkeypatch.setattr(api.app.state, "settings", Settings(execution_mode="worker"), raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200
    assert response.json()["status_notice"] is None
