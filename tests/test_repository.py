"""仓储 CRUD：InMemory 与 SqlRepository(SQLite) 跑同一套用例，保证两实现行为一致。

SQLite 用 StaticPool 共享单连接，使 :memory: 在多次 session 间保持同一张库。
真实 PostgreSQL 的集成测试见 test_repository_pg（标 @pytest.mark.pg）。
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy import event as sa_event
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
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
from deep_research.persistence.db import (
    create_all,
    make_engine,
    make_sessionmaker,
    prepare_sqlite_schema,
)
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.persistence.orm import Base
from deep_research.persistence.repository import LeaseLostError
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
                        source_title="Annual report",
                        evidence_context="Context before verbatim source evidence and after.",
                        reason="quote_found_in_source",
                        semantic_status="supported",
                        semantic_confidence=0.87,
                        semantic_reason="quote directly supports statement",
                        claim_id="claim-a",
                        consistency_status="conflicted",
                        contradicts_claim_ids=["claim-b"],
                        contradiction_reason="opposite trend",
                        corroboration_status="disputed",
                        independent_source_count=2,
                        corroborates_claim_ids=["claim-c"],
                        corroboration_reason="support exists, but another source conflicts",
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
    assert detail.results[0].findings[0].verification.source_title == "Annual report"
    assert (
        detail.results[0].findings[0].verification.evidence_context
        == "Context before verbatim source evidence and after."
    )
    assert detail.results[0].findings[0].verification.semantic_status == "supported"
    assert detail.results[0].findings[0].verification.semantic_confidence == 0.87
    assert detail.results[0].findings[0].verification.claim_id == "claim-a"
    assert detail.results[0].findings[0].verification.consistency_status == "conflicted"
    assert detail.results[0].findings[0].verification.contradicts_claim_ids == ["claim-b"]
    assert detail.results[0].findings[0].verification.corroboration_status == "disputed"
    assert detail.results[0].findings[0].verification.independent_source_count == 2
    assert detail.results[0].findings[0].verification.corroborates_claim_ids == ["claim-c"]
    assert detail.total_tokens == 42
    assert detail.orchestration is not None
    assert detail.orchestration.workflow_name == "deep"
    assert detail.orchestration.steps[0].status == StepStatus.SUCCEEDED
    assert detail.orchestration.checkpoint["query"] == "Q"
    assert detail.orchestration.definition["name"] == "deep"
    assert await repo.get_run_status(run_id) == "done"
    assert await repo.get_run_status("missing") is None

    events = await repo.get_events(run_id)
    assert len(events) == 2
    assert await repo.get_events(run_id, after_seq=1) == events[1:]

    summaries = await repo.list_runs()
    assert summaries[0].id == run_id


@pytest.mark.asyncio
async def test_replace_artifacts_is_idempotent(repo):
    run_id = await repo.create_run("replace")
    plan = ResearchPlan(
        interpretation="initial",
        sub_questions=[SubQuestion(question="plan question")],
    )
    reflection = [SubQuestion(question="follow-up")]
    result = ResearchResult(
        sub_question="plan question",
        findings=[Finding(statement="s", source_url="https://a.example")],
    )
    report = Report(query="replace", markdown="# report", citations=[])

    for _ in range(2):
        await repo.replace_artifacts(
            run_id,
            plan=plan,
            reflection_rounds=[(1, reflection)],
            results=[result],
            report=report,
        )

    detail = await repo.get_run(run_id)
    assert detail is not None
    assert detail.interpretation == "initial"
    assert [item.question for item in detail.sub_questions] == ["plan question", "follow-up"]
    assert len(detail.results) == 1
    assert detail.report is not None and detail.report.markdown == "# report"


@pytest.mark.asyncio
async def test_lease_fences_writes_after_ownership_changes(repo):
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": "lease fencing"})
    runtime.save_checkpoint(
        {"query": "lease fencing", "scratch": {}},
        {"name": "deep", "steps": []},
    )
    run_id = await repo.create_run("lease fencing", execution=execution, lease_owner="worker-a")

    await repo.set_status(run_id, "running", lease_owner="worker-a")
    assert await repo.acquire_lease(run_id, "worker-b") is False
    await repo.release_lease(run_id, "worker-a")
    assert await repo.acquire_lease(run_id, "worker-b") is True

    with pytest.raises(LeaseLostError):
        await repo.set_status(run_id, "error", lease_owner="worker-a")
    with pytest.raises(LeaseLostError):
        await repo.save_events(
            run_id,
            [Event(stage="ORCHESTRATOR", type="error")],
            lease_owner="worker-a",
        )
    with pytest.raises(LeaseLostError):
        await repo.finalize(
            run_id,
            elapsed=99,
            total_tokens=99,
            lease_owner="worker-a",
        )

    detail = await repo.get_run(run_id)
    assert detail is not None
    assert detail.status == "running"
    assert detail.total_tokens == 0

    await repo.finalize(
        run_id,
        elapsed=1,
        total_tokens=2,
        lease_owner="worker-b",
    )
    detail = await repo.get_run(run_id)
    assert detail is not None
    assert detail.status == "done"
    assert detail.total_tokens == 2


@pytest.mark.asyncio
async def test_prepare_resume_atomically_reopens_run_and_removes_old_terminal(repo):
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": "resume"})
    runtime.save_checkpoint(
        {"query": "resume", "scratch": {}},
        {"name": "deep", "steps": []},
    )
    owner = "resume-owner"
    run_id = await repo.create_run("resume", execution=execution, lease_owner=owner)
    await repo.set_status(run_id, "error", lease_owner=owner)
    await repo.save_events(
        run_id,
        [
            Event(stage="PLANNER", type="info", message="keep"),
            Event(stage="ORCHESTRATOR", type="error", message="old terminal"),
        ],
        lease_owner=owner,
    )

    await repo.prepare_resume(run_id, lease_owner=owner)

    assert await repo.get_run_status(run_id) == "running"
    events = await repo.get_events(run_id)
    assert [(event.stage, event.type, event.message) for event in events] == [
        ("PLANNER", "info", "keep")
    ]


async def _create_legacy_research_run_table(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE research_run (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    query TEXT NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    interpretation TEXT NOT NULL,
                    elapsed FLOAT NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    finished_at DATETIME
                )
                """
            )
        )


_REVISION_TABLES = (
    "research_run",
    "sub_question",
    "research_result",
    "finding",
    "source",
    "report",
    "event",
    "run_tag",
    "model_profile",
    "agent_card",
    "search_key",
    "workflow_def",
    "workflow_run",
    "step_run",
)


def _revision_schema_snapshot(connection: Connection) -> dict[str, dict[str, object]]:
    inspector = inspect(connection)
    snapshot: dict[str, dict[str, object]] = {}
    for table_name in _REVISION_TABLES:
        snapshot[table_name] = {
            "columns": {
                column["name"]: (
                    str(column["type"]),
                    bool(column["nullable"]),
                    column.get("default"),
                )
                for column in inspector.get_columns(table_name)
            },
            "unique": sorted(
                (
                    str(constraint.get("name") or ""),
                    tuple(constraint.get("column_names") or []),
                )
                for constraint in inspector.get_unique_constraints(table_name)
            ),
            "indexes": sorted(
                (
                    str(index.get("name") or ""),
                    tuple(index.get("column_names") or []),
                    bool(index.get("unique")),
                )
                for index in inspector.get_indexes(table_name)
            ),
            "foreign_keys": sorted(
                (
                    tuple(foreign_key.get("constrained_columns") or []),
                    str(foreign_key.get("referred_table") or ""),
                    tuple(foreign_key.get("referred_columns") or []),
                    str((foreign_key.get("options") or {}).get("ondelete") or ""),
                )
                for foreign_key in inspector.get_foreign_keys(table_name)
            ),
        }
    return snapshot


async def _read_revision_schema(engine: AsyncEngine) -> dict[str, dict[str, object]]:
    async with engine.begin() as conn:
        return await conn.run_sync(_revision_schema_snapshot)


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+aiosqlite://",
        "sqlite+aiosqlite:///:memory:",
        "sqlite+aiosqlite:///file:prepare-schema?mode=memory&cache=shared&uri=true",
        "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true",
    ],
)
@pytest.mark.asyncio
async def test_prepare_sqlite_schema_creates_tables_for_memory_urls(database_url):
    engine = make_engine(database_url)
    try:
        await prepare_sqlite_schema(engine, database_url)

        async with engine.begin() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )

        assert {"research_run", "workflow_run"} <= tables
        assert "alembic_version" not in tables
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_prepare_sqlite_schema_accepts_current_create_all_database(tmp_path):
    db_path = tmp_path / "current-create-all.db"
    database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    engine = make_engine(database_url)
    await create_all(engine)
    repo = SqlRepository(make_sessionmaker(engine))
    run_id = await repo.create_run("current create_all data")
    await repo.save_result(
        run_id,
        ResearchResult(
            sub_question="preserve evidence",
            findings=[
                Finding(
                    statement="Existing evidence survives schema preparation.",
                    source_url="https://example.com/existing",
                    evidence_quote="existing quote",
                    verification=EvidenceVerification(
                        status="verified",
                        method="normalized_quote",
                        source_content_hash="existing-hash",
                        source_title="Existing source",
                        evidence_context="Context around the existing quote.",
                        reason="quote_found_in_source",
                    ),
                )
            ],
        ),
    )

    await prepare_sqlite_schema(engine, database_url)

    verification_engine = make_engine(database_url)
    try:
        async with verification_engine.begin() as conn:
            columns = await conn.run_sync(
                lambda sync_conn: {
                    column["name"] for column in inspect(sync_conn).get_columns("finding")
                }
            )
            version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        detail = await SqlRepository(make_sessionmaker(verification_engine)).get_run(run_id)
        assert {
            "source_title",
            "evidence_context",
            "corroboration_status",
            "independent_source_count",
            "corroborates_claim_ids",
            "corroboration_reason",
        } <= columns
        assert version == "0016"
        assert detail is not None
        verification = detail.results[0].findings[0].verification
        assert verification.source_title == "Existing source"
        assert verification.evidence_context == "Context around the existing quote."
    finally:
        await verification_engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_does_not_create_future_orm_tables(tmp_path):
    future_table = sa.Table(
        "future_table_after_0014",
        Base.metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    db_path = tmp_path / "frozen-revision.db"
    database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    engine = make_engine(database_url)
    try:
        await _create_legacy_research_run_table(engine)
        await prepare_sqlite_schema(engine, database_url)

        async with engine.begin() as conn:
            tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())

        assert "workflow_run" in tables
        assert future_table.name not in tables
    finally:
        Base.metadata.remove(future_table)
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_missing_tables_match_clean_migration_schema(tmp_path):
    clean_path = tmp_path / "clean.db"
    legacy_path = tmp_path / "legacy-minimal.db"
    clean_url = f"sqlite+aiosqlite:///{clean_path.as_posix()}"
    legacy_url = f"sqlite+aiosqlite:///{legacy_path.as_posix()}"
    clean_engine = make_engine(clean_url)
    legacy_engine = make_engine(legacy_url)
    try:
        await _create_legacy_research_run_table(legacy_engine)
        await prepare_sqlite_schema(clean_engine, clean_url)
        await prepare_sqlite_schema(legacy_engine, legacy_url)

        clean_schema = await _read_revision_schema(clean_engine)
        legacy_schema = await _read_revision_schema(legacy_engine)

        assert legacy_schema == clean_schema
        model_columns = legacy_schema["model_profile"]["columns"]
        model_uniques = legacy_schema["model_profile"]["unique"]
        workflow_columns = legacy_schema["workflow_def"]["columns"]
        assert isinstance(model_columns, dict)
        assert isinstance(model_uniques, list)
        assert isinstance(workflow_columns, dict)
        assert model_columns["api_key"][2] == "''"
        assert model_columns["parameter_mode"][2] == "'temperature'"
        assert ("uq_model_profile_name", ("name",)) in model_uniques
        assert workflow_columns["nodes"][2] == "'[]'"
        assert workflow_columns["version"][2] == "'1'"
    finally:
        await clean_engine.dispose()
        await legacy_engine.dispose()


@pytest.mark.asyncio
async def test_prepare_sqlite_schema_repairs_legacy_finding_columns(tmp_path):
    db_path = tmp_path / "legacy.db"
    database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    engine = make_engine(database_url)
    await create_all(engine)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE finding"))
        await conn.execute(
            text(
                """
                CREATE TABLE finding (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    result_id VARCHAR(36) NOT NULL,
                    statement TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    confidence FLOAT NOT NULL DEFAULT 0.7,
                    FOREIGN KEY(result_id) REFERENCES research_result (id) ON DELETE CASCADE
                )
                """
            )
        )

    await prepare_sqlite_schema(engine, database_url)
    repo = SqlRepository(make_sessionmaker(engine))
    run_id = await repo.create_run("legacy")
    await repo.save_result(
        run_id,
        ResearchResult(
            sub_question="a",
            findings=[
                Finding(
                    statement="s",
                    source_url="https://a.com",
                    evidence_quote="legacy quote",
                    verification=EvidenceVerification(
                        status="verified",
                        method="normalized_quote",
                        source_content_hash="hash",
                        source_title="Legacy source",
                        evidence_context="Legacy context around legacy quote.",
                        reason="ok",
                        semantic_status="supported",
                        semantic_confidence=0.9,
                        semantic_reason="ok",
                        claim_id="c1",
                        consistency_status="clear",
                    ),
                )
            ],
        ),
    )
    detail = await repo.get_run(run_id)
    assert detail is not None
    assert detail.results[0].findings[0].evidence_quote == "legacy quote"
    assert detail.results[0].findings[0].verification.claim_id == "c1"
    assert detail.results[0].findings[0].verification.source_title == "Legacy source"
    assert (
        detail.results[0].findings[0].verification.evidence_context
        == "Legacy context around legacy quote."
    )

    async with engine.begin() as conn:
        version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    assert version == "0016"
    await engine.dispose()


@pytest.mark.asyncio
async def test_prepare_sqlite_schema_reconciles_falsely_stamped_head(tmp_path):
    db_path = tmp_path / "false-head.db"
    database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    engine = make_engine(database_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE research_run (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    query TEXT NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    interpretation TEXT NOT NULL,
                    elapsed FLOAT NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    finished_at DATETIME
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE sub_question (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    run_id VARCHAR(36) NOT NULL,
                    idx INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    depends_on JSON NOT NULL,
                    origin VARCHAR(16) NOT NULL,
                    round INTEGER NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES research_run (id) ON DELETE CASCADE
                )
                """
            )
        )
        await conn.execute(text("CREATE INDEX ix_sub_question_run_id ON sub_question (run_id)"))
        # A legacy database may have a covering (but non-unique) composite
        # index.  The reconciliation must still add the actual invariant.
        await conn.execute(
            text("CREATE INDEX ix_sub_question_run_idx ON sub_question (run_id, idx)")
        )
        await conn.execute(
            text(
                "INSERT INTO research_run "
                "(id, query, status, interpretation, elapsed, total_tokens) "
                "VALUES ('run-1', 'Q', 'error', '', 0, 0)"
            )
        )
        for row_id in ("sq-1", "sq-2"):
            await conn.execute(
                text(
                    "INSERT INTO sub_question "
                    "(id, run_id, idx, question, rationale, depends_on, origin, round) "
                    "VALUES (:id, 'run-1', 0, :id, '', '[]', 'plan', 0)"
                ),
                {"id": row_id},
            )
        await conn.execute(
            text(
                """
                CREATE TABLE model_profile (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    name VARCHAR(64) NOT NULL UNIQUE,
                    base_url TEXT,
                    api_key TEXT NOT NULL DEFAULT '',
                    model VARCHAR(100) NOT NULL DEFAULT 'gpt-4o-mini',
                    temperature FLOAT NOT NULL DEFAULT 0.3,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE workflow_def (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    name VARCHAR(64) NOT NULL UNIQUE,
                    display_name VARCHAR(100) NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    steps JSON NOT NULL DEFAULT '[]',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await conn.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        )
        await conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0013')"))

    await prepare_sqlite_schema(engine, database_url)

    async with engine.begin() as conn:
        schema = await conn.run_sync(
            lambda sync_conn: {
                "model": {c["name"] for c in inspect(sync_conn).get_columns("model_profile")},
                "workflow": {c["name"] for c in inspect(sync_conn).get_columns("workflow_def")},
                "unique": inspect(sync_conn).get_unique_constraints("sub_question"),
            }
        )
        version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        repaired_indexes = list(
            (
                await conn.execute(
                    text("SELECT idx FROM sub_question WHERE run_id = 'run-1' ORDER BY idx")
                )
            ).scalars()
        )

    assert {"parameter_mode", "reasoning_effort"} <= schema["model"]
    assert {"nodes", "edges", "viewport", "version"} <= schema["workflow"]
    assert any(
        set(constraint.get("column_names") or []) == {"run_id", "idx"}
        for constraint in schema["unique"]
    )
    assert version == "0016"
    assert repaired_indexes == [0, 1]
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_missing_run(repo):
    assert await repo.get_run("does-not-exist") is None
    assert await repo.get_events("does-not-exist") == []
    assert await repo.acquire_lease("does-not-exist", "worker") is False
    assert await repo.renew_lease("does-not-exist", "worker") is False
    await repo.release_lease("does-not-exist", "worker")


@pytest.mark.asyncio
async def test_recovery_lease_is_exclusive(repo):
    run_id = await repo.create_run("lease")
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": "lease"})
    await repo.save_orchestration(run_id, execution)

    assert await repo.acquire_lease(run_id, "worker-a") is True
    assert await repo.acquire_lease(run_id, "worker-b") is False
    assert await repo.renew_lease(run_id, "worker-a") is True
    await repo.release_lease(run_id, "worker-a")
    assert await repo.acquire_lease(run_id, "worker-b") is True


@pytest.mark.asyncio
async def test_expired_or_released_lease_cannot_be_renewed(repo) -> None:
    run_id = await repo.create_run("lease-aba")
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": "lease-aba"})
    await repo.save_orchestration(run_id, execution)

    assert await repo.acquire_lease(run_id, "worker-a", seconds=0) is True
    assert await repo.renew_lease(run_id, "worker-a") is False
    assert await repo.acquire_lease(run_id, "worker-b") is True
    await repo.release_lease(run_id, "worker-b")

    # A stale heartbeat must not resurrect its old token after the successor
    # completed and released the lease.
    assert await repo.renew_lease(run_id, "worker-a") is False


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
