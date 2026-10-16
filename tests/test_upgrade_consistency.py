"""Contracts discovered in the 2026-10-16 review, using isolated repositories."""

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event

from deep_research import api
from deep_research.config import Settings
from deep_research.library.repository import SqlLibraryRepository
from deep_research.observability import Event
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.persistence.repository import RunHasActiveChildren, RunQuotaExceeded
from deep_research.persistence.sql_repository import SqlRepository
from deep_research.workbench.qa_store import InMemoryQaStore, QaMessage, SqlQaStore
from deep_research.workbench.usage import usage_today


@pytest.fixture(params=["memory", "sqlite"])
async def repository(request, tmp_path):
    if request.param == "memory":
        yield InMemoryRepository()
    else:
        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'isolated.db'}")
        await create_all(engine)
        try:
            yield SqlRepository(make_sessionmaker(engine))
        finally:
            await engine.dispose()


@pytest.mark.parametrize("template,strategy", [("autoResearch", "quick"), ("litReview", "quick")])
async def test_template_retry_keeps_submitted_hash(repository, template, strategy, monkeypatch):
    monkeypatch.setattr(api, "_run_limiter", api._RateLimiter(100, 60))
    app = FastAPI()
    app.post("/api/runs", status_code=202)(api.create_run)
    app.state.repo = repository
    app.state.settings = Settings(intent_enabled=False, api_key="", api_credentials=())
    app.state.catalog = None
    app.state.inline_worker = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as c:
        payload = {
            "query": "Review reconstruction methods",
            "template": template,
            "strategy": strategy,
        }
        headers = {"Idempotency-Key": "stable-request"}
        first = await c.post("/api/runs", json=payload, headers=headers)
        second = await c.post("/api/runs", json=payload, headers=headers)
        assert first.status_code == second.status_code == 202
        assert first.json() == second.json()
        assert second.headers["Idempotency-Replayed"] == "true"
        changed = await c.post("/api/runs", json={**payload, "query": "Changed"}, headers=headers)
        assert changed.status_code == 409


async def test_atomic_quota_and_deleted_run_accounting(repository):
    async def submit(i):
        return await repository.create_run_once(
            f"q{i}",
            request_hash=str(i),
            owner_id="owner",
            daily_run_quota=1,
            idempotency_key=f"request-{i}",
        )

    results = await asyncio.gather(*(submit(i) for i in range(5)), return_exceptions=True)
    winners = [r for r in results if isinstance(r, tuple)]
    assert len(winners) == 1
    assert sum(isinstance(r, RunQuotaExceeded) for r in results) == 4
    run_id = winners[0][0]
    await repository.finalize(run_id, elapsed=0, total_tokens=123)
    assert await usage_today(repository, "owner") == {"runs": 1, "tokens": 123}
    assert await repository.delete_run(run_id)
    assert await usage_today(repository, "owner") == {"runs": 1, "tokens": 123}
    with pytest.raises(RunQuotaExceeded):
        await submit(99)
    await repository.create_run_once("other", request_hash="o", owner_id="other", daily_run_quota=1)


async def test_replay_does_not_consume_quota(repository):
    first = await repository.create_run_once(
        "q", request_hash="h", idempotency_key="k", owner_id="a", daily_run_quota=1
    )
    replay = await repository.create_run_once(
        "q", request_hash="h", idempotency_key="k", owner_id="a", daily_run_quota=1
    )
    assert first == (replay[0], True) and replay[1] is False


async def test_safety_metrics_include_events_past_display_limit(repository):
    run_id = await repository.create_run("q")
    events = [Event(stage="RESEARCHER", type="info", message="progress") for _ in range(2000)]
    events.append(
        Event(stage="RESEARCHER", type="info", data={"category": "source_policy", "blocked": 7})
    )
    await repository.save_events(run_id, events)
    detail = await api._enrich_run_detail(repository, await repository.get_run(run_id))
    assert len(detail.events) == 2000 and detail.metrics.blocked_sources == 7
    # Compatibility overwrite must replace the metric, not add to an old counter.
    await repository.save_events(
        run_id,
        [Event(stage="RESEARCHER", type="info", data={"category": "source_policy", "blocked": 2})],
    )
    detail = await api._enrich_run_detail(repository, await repository.get_run(run_id))
    assert detail.metrics.blocked_sources == 2


async def test_task_deletion_waits_for_qa_then_cascades(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'qa.db'}")
    await create_all(engine)
    try:
        sessions = make_sessionmaker(engine)
        repo, qa = SqlRepository(sessions), SqlQaStore(sessions)
        run_id = await repo.create_run("q")
        conv = await qa.create("a", "bound", run_id)
        standalone = await qa.create("a", "standalone")
        await qa.append(
            conv.id, QaMessage(id="", position=0, query="q", answer="", status="running")
        )
        with pytest.raises(RunHasActiveChildren):
            await repo.delete_run(run_id)
        await qa.delete(conv.id)
        conv = await qa.create("a", "completed", run_id)
        await qa.append(conv.id, QaMessage(id="", position=0, query="q", answer="a"))
        assert await repo.delete_run(run_id)
        assert await qa.get(conv.id) is None
        assert await qa.get(standalone.id) is not None
        with pytest.raises(KeyError):
            await qa.create("a", "late", run_id)
    finally:
        await engine.dispose()


async def test_single_project_does_not_load_unrelated_projects(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'library.db'}")
    await create_all(engine)
    try:
        library = SqlLibraryRepository(make_sessionmaker(engine))
        project = await library.create_project("a", "A")
        await library.create_project("b", "B")
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", capture)
        result = await library.get_project(project.id)
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
        assert result.id == project.id and result.corpus_count == 1
        assert statements and all("WHERE" in sql.upper() for sql in statements)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("storage", ["memory", "sqlite"])
async def test_conversation_paging_reaches_all_records_with_tied_timestamps(tmp_path, storage):
    from datetime import UTC, datetime

    from sqlalchemy import update

    from deep_research.persistence.orm import QaConversationRow

    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'paging.db'}")
    await create_all(engine)
    try:
        sessions = make_sessionmaker(engine)
        store = InMemoryQaStore() if storage == "memory" else SqlQaStore(sessions)
        stamp = datetime(2026, 1, 1, tzinfo=UTC)
        expected = set()
        for i in range(201):
            conversation = await store.create("a", str(i))
            expected.add(conversation.id)
        await store.create("b", "other owner")
        if storage == "memory":
            for item in store._items.values():
                item.updated_at = stamp
        else:
            async with sessions() as session, session.begin():
                await session.execute(update(QaConversationRow).values(updated_at=stamp))
        seen = []
        before = None
        while True:
            page = await store.list("a", limit=30, before=before)
            seen.extend(item.id for item in page)
            if len(page) < 30:
                break
            tail = page[-1]
            before = (stamp, tail.id)
        assert len(seen) == len(set(seen)) == 201
        assert set(seen) == expected
    finally:
        await engine.dispose()
