"""仓储 CRUD：InMemory 与 SqlRepository(SQLite) 跑同一套用例，保证两实现行为一致。

SQLite 用 StaticPool 共享单连接，使 :memory: 在多次 session 间保持同一张库。
真实 PostgreSQL 的集成测试见 test_repository_pg（标 @pytest.mark.pg）。
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from deep_research.models import Finding, Report, ResearchPlan, ResearchResult, SubQuestion
from deep_research.observability import Event
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.persistence.sql_repository import SqlRepository


@pytest.fixture(params=["memory", "sqlite"])
async def repo(request):
    if request.param == "memory":
        yield InMemoryRepository()
        return
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    await create_all(engine)
    yield SqlRepository(make_sessionmaker(engine))
    await engine.dispose()


@pytest.mark.asyncio
async def test_crud_roundtrip(repo):
    run_id = await repo.create_run("Q")
    await repo.set_status(run_id, "running")
    await repo.save_plan(
        run_id,
        ResearchPlan(
            interpretation="解读",
            sub_questions=[SubQuestion(question="a"), SubQuestion(question="b", depends_on=[0])],
        ),
    )
    await repo.save_result(
        run_id,
        ResearchResult(
            sub_question="a", findings=[Finding(statement="s", source_url="https://a.com")]
        ),
    )
    await repo.save_report(run_id, Report(query="Q", markdown="# R", citations=["https://a.com"]))
    await repo.save_events(
        run_id,
        [Event(stage="PLANNER", type="start"), Event(stage="ORCHESTRATOR", type="done")],
    )
    await repo.finalize(run_id, elapsed=2.5, total_tokens=42)

    detail = await repo.get_run(run_id)
    assert detail is not None
    assert detail.status == "done"
    assert detail.interpretation == "解读"
    assert len(detail.sub_questions) == 2
    assert detail.sub_questions[1].depends_on == [0]
    assert detail.report is not None
    assert detail.report.citations == ["https://a.com"]
    assert detail.total_tokens == 42

    events = await repo.get_events(run_id)
    assert len(events) == 2
    assert await repo.get_events(run_id, after_seq=1) == events[1:]

    summaries = await repo.list_runs()
    assert summaries[0].id == run_id


@pytest.mark.asyncio
async def test_get_missing_run(repo):
    assert await repo.get_run("does-not-exist") is None
    assert await repo.get_events("does-not-exist") == []


@pytest.mark.pg
@pytest.mark.asyncio
async def test_sql_repository_on_postgres():
    """真实 PostgreSQL 冒烟测试（CI 用 service container；本地无 PG 时跳过）。"""
    url = os.getenv("DATABASE_URL")
    if not url or "postgresql" not in url:
        pytest.skip("未配置 PostgreSQL 的 DATABASE_URL")
    engine = make_engine(url)
    await create_all(engine)
    repo = SqlRepository(make_sessionmaker(engine))
    run_id = await repo.create_run("PG 冒烟")
    await repo.save_report(run_id, Report(query="PG 冒烟", markdown="# ok", citations=[]))
    await repo.finalize(run_id, elapsed=0.1, total_tokens=1)
    detail = await repo.get_run(run_id)
    assert detail is not None and detail.status == "done"
    await engine.dispose()
