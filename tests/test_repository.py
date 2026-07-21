"""仓储 CRUD：InMemory 与 SqlRepository(SQLite) 跑同一套用例，保证两实现行为一致。

SQLite 用 StaticPool 共享单连接，使 :memory: 在多次 session 间保持同一张库。
真实 PostgreSQL 的集成测试见 test_repository_pg（标 @pytest.mark.pg）。
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from deep_research.models import (
    EvidenceVerification,
    Finding,
    Report,
    ResearchPlan,
    ResearchResult,
    SubQuestion,
)
from deep_research.observability import Event
from deep_research.orchestration import OrchestrationRuntime, RunStatus, StepStatus
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

    # 开启外键强制，使 delete_run 的 ondelete=CASCADE 真正级联（与 make_engine 生产路径一致）
    @sa_event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

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
            sub_question="a",
            findings=[
                Finding(
                    statement="s",
                    source_url="https://a.com",
                    evidence_quote="verbatim source evidence",
                    verification=EvidenceVerification(
                        status="verified",
                        method="normalized_quote",
                        source_content_hash="abc123",
                        reason="quote_found_in_source",
                        semantic_status="supported",
                        semantic_confidence=0.87,
                        semantic_reason="quote directly supports statement",
                        claim_id="claim-a",
                        consistency_status="conflicted",
                        contradicts_claim_ids=["claim-b"],
                        contradiction_reason="opposite trend",
                    ),
                )
            ],
        ),
    )
    await repo.save_report(run_id, Report(query="Q", markdown="# R", citations=["https://a.com"]))
    await repo.save_events(
        run_id,
        [Event(stage="PLANNER", type="start"), Event(stage="ORCHESTRATOR", type="done")],
    )
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": "Q"})
    step = runtime.create_step(label="planner", kind="agent", agent="planner")
    runtime.start_step(step)
    runtime.complete_step(step)
    runtime.save_checkpoint({"query": "Q", "scratch": {}}, {"name": "deep", "steps": []})
    runtime.finish(RunStatus.SUCCEEDED, {"has_report": True})
    await repo.save_orchestration(run_id, execution)
    await repo.finalize(run_id, elapsed=2.5, total_tokens=42)

    detail = await repo.get_run(run_id)
    assert detail is not None
    assert detail.status == "done"
    assert detail.interpretation == "解读"
    assert len(detail.sub_questions) == 2
    assert detail.sub_questions[1].depends_on == [0]
    assert detail.report is not None
    assert detail.report.citations == ["https://a.com"]
    assert detail.results[0].findings[0].evidence_quote == "verbatim source evidence"
    assert detail.results[0].findings[0].verification.status == "verified"
    assert detail.results[0].findings[0].verification.source_content_hash == "abc123"
    assert detail.results[0].findings[0].verification.semantic_status == "supported"
    assert detail.results[0].findings[0].verification.semantic_confidence == 0.87
    assert detail.results[0].findings[0].verification.claim_id == "claim-a"
    assert detail.results[0].findings[0].verification.consistency_status == "conflicted"
    assert detail.results[0].findings[0].verification.contradicts_claim_ids == ["claim-b"]
    assert detail.total_tokens == 42
    assert detail.orchestration is not None
    assert detail.orchestration.workflow_name == "deep"
    assert detail.orchestration.steps[0].status == StepStatus.SUCCEEDED
    assert detail.orchestration.checkpoint["query"] == "Q"
    assert detail.orchestration.definition["name"] == "deep"

    events = await repo.get_events(run_id)
    assert len(events) == 2
    assert await repo.get_events(run_id, after_seq=1) == events[1:]

    summaries = await repo.list_runs()
    assert summaries[0].id == run_id


@pytest.mark.asyncio
async def test_get_missing_run(repo):
    assert await repo.get_run("does-not-exist") is None
    assert await repo.get_events("does-not-exist") == []


@pytest.mark.asyncio
async def test_recovery_lease_is_exclusive(repo):
    run_id = await repo.create_run("lease")
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": "lease"})
    await repo.save_orchestration(run_id, execution)

    assert await repo.acquire_lease(run_id, "worker-a") is True
    assert await repo.acquire_lease(run_id, "worker-b") is False
    assert await repo.acquire_lease(run_id, "worker-a") is True
    await repo.release_lease(run_id, "worker-a")
    assert await repo.acquire_lease(run_id, "worker-b") is True


@pytest.mark.asyncio
async def test_delete_run_cascades(repo):
    run_id = await repo.create_run("待删")
    await repo.save_report(run_id, Report(query="待删", markdown="# R", citations=[]))
    await repo.save_events(run_id, [Event(stage="PLANNER", type="start")])
    await repo.set_tags(run_id, ["x"])

    assert await repo.delete_run(run_id) is True
    assert await repo.get_run(run_id) is None
    assert await repo.get_events(run_id) == []  # 子表随之清空
    assert await repo.list_tags() == []
    assert await repo.delete_run(run_id) is False  # 再删返回 False


@pytest.mark.asyncio
async def test_set_tags_and_filter_by_tag(repo):
    a = await repo.create_run("Python 并发")
    b = await repo.create_run("Rust 所有权")
    await repo.set_tags(a, ["lang", "py", " py ", ""])  # 去空白 / 去重 / 丢空串
    await repo.set_tags(b, ["lang", "rust"])

    da = await repo.get_run(a)
    assert da is not None and sorted(da.tags) == ["lang", "py"]

    summaries = {s.id: s for s in await repo.list_runs()}
    assert sorted(summaries[a].tags) == ["lang", "py"]

    assert {s.id for s in await repo.list_runs(tag="lang")} == {a, b}
    assert {s.id for s in await repo.list_runs(tag="py")} == {a}

    counts = {t.tag: t.count for t in await repo.list_tags()}
    assert counts == {"lang": 2, "py": 1, "rust": 1}

    await repo.set_tags(a, ["only"])  # 替换语义
    da2 = await repo.get_run(a)
    assert da2 is not None and da2.tags == ["only"]


@pytest.mark.asyncio
async def test_list_runs_status_and_query_filter(repo):
    a = await repo.create_run("机器学习入门")
    b = await repo.create_run("深度学习进阶")
    await repo.finalize(a, elapsed=1.0, total_tokens=1)  # a → done；b 保持 pending

    assert {s.id for s in await repo.list_runs(status="done")} == {a}
    assert {s.id for s in await repo.list_runs(status="pending")} == {b}
    assert {s.id for s in await repo.list_runs(q="学习")} == {a, b}
    assert {s.id for s in await repo.list_runs(q="深度")} == {b}


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
