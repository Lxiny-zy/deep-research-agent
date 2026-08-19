"""PostgreSQL-only migration and deployment-lock integration tests.

These tests intentionally use real asyncpg sessions.  Unit tests around
``upgrade_head`` verify SQL generation, while this module verifies the
database guarantees that cannot be reproduced with fakes: existing rows
survive the 0017 -> 0018 upgrade and two independent deployers serialize on
the same PostgreSQL advisory lock.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from deep_research import migrate
from deep_research.persistence.db import _migration_root, make_engine


def _postgres_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.skip("未配置 PostgreSQL DATABASE_URL")
    return url


def _alembic_config(database_url: str) -> Config:
    root = _migration_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["database_url"] = database_url
    return config


async def _run_migration(database_url: str, revision: str) -> None:
    """Run Alembic in a worker because its command API is synchronous."""

    await asyncio.to_thread(command.upgrade, _alembic_config(database_url), revision)


@asynccontextmanager
async def _isolated_database(base_url: str) -> AsyncIterator[str]:
    """Create a disposable database on the CI PostgreSQL service."""

    database = f"migration_test_{uuid4().hex}"
    # CREATE/DROP DATABASE must run outside a transaction.
    parsed_url = make_url(base_url)
    admin_url = parsed_url.set(database="postgres")
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    created = False
    try:
        try:
            async with admin.connect() as connection:
                await connection.execute(text(f'CREATE DATABASE "{database}"'))
            created = True
        except SQLAlchemyError as exc:
            if getattr(getattr(exc, "orig", None), "sqlstate", None) == "42501":
                pytest.skip(f"PostgreSQL 用户无权创建临时数据库: {exc}")
            raise
        yield str(parsed_url.set(database=database))
    finally:
        if created:
            async with admin.connect() as connection:
                # Terminate any leaked test sessions before dropping the database.
                await connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :database AND pid <> pg_backend_pid()"
                    ),
                    {"database": database},
                )
                await connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
        await admin.dispose()


@pytest.mark.pg
@pytest.mark.asyncio
async def test_postgres_upgrade_0017_to_0018_preserves_historical_rows() -> None:
    """Upgrade a populated 0017 schema and verify 0018 defaults/contracts."""

    base_url = _postgres_url()
    async with _isolated_database(base_url) as database_url:
        # Build a populated schema at 0016 first, then explicitly cross the
        # 0017 -> 0018 boundary.  The source row proves that 0017 backfills
        # hashes, while the event/workflow rows prove that 0018 adds defaults
        # without disturbing existing data.
        await _run_migration(database_url, "0016")
        engine = make_engine(database_url)
        run_id = str(uuid4())
        workflow_id = str(uuid4())
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO research_run "
                        "(id, query, status, interpretation, elapsed, total_tokens) "
                        "VALUES (:id, 'historical query', 'done', '', 1.5, 7)"
                    ),
                    {"id": run_id},
                )
                await connection.execute(
                    text(
                        "INSERT INTO source (id, run_id, title, url, content) "
                        "VALUES (:id, :run_id, 'historical source', "
                        "'https://example.test/a', 'old content')"
                    ),
                    {"id": str(uuid4()), "run_id": run_id},
                )

            await _run_migration(database_url, "0017")

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO event "
                        "(id, run_id, seq, stage, type, message, elapsed, data) "
                        "VALUES (:id, :run_id, 0, 'PLANNER', 'info', 'historical', 0, '{}'::json)"
                    ),
                    {"id": str(uuid4()), "run_id": run_id},
                )
                await connection.execute(
                    text(
                        "INSERT INTO workflow_run "
                        "(id, research_run_id, workflow_name, status, input, output) "
                        "VALUES (:id, :run_id, 'deep', 'done', '{}'::json, '{}'::json)"
                    ),
                    {"id": workflow_id, "run_id": run_id},
                )

            await _run_migration(database_url, "head")
            async with engine.connect() as connection:
                revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                row = (
                    await connection.execute(
                        text(
                            "SELECT idempotency_key, request_hash FROM research_run WHERE id = :id"
                        ),
                        {"id": run_id},
                    )
                ).one()
                event_attempt = await connection.scalar(
                    text("SELECT attempt FROM event WHERE run_id = :run_id AND seq = 0"),
                    {"run_id": run_id},
                )
                workflow_attempt = await connection.scalar(
                    text("SELECT attempt FROM workflow_run WHERE id = :id"), {"id": workflow_id}
                )
                source_hash = await connection.scalar(
                    text("SELECT content_hash FROM source WHERE run_id = :run_id"),
                    {"run_id": run_id},
                )
            assert revision == "0018"
            assert row == (None, None)
            assert event_attempt == 1
            assert workflow_attempt == 1
            assert source_hash == hashlib.sha256(b"old content").hexdigest()
        finally:
            await engine.dispose()


@pytest.mark.pg
@pytest.mark.asyncio
async def test_postgres_migration_advisory_lock_serializes_two_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two real ``upgrade_head`` callers must serialize on PostgreSQL."""

    database_url = _postgres_url()
    observer = make_engine(database_url)
    key = migrate._POSTGRES_MIGRATION_LOCK
    classid, objid = key >> 32, key & 0xFFFFFFFF
    started = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    call_order: list[int] = []
    order_lock = threading.Lock()

    def controlled_upgrade(_database_url: str) -> None:
        with order_lock:
            index = len(call_order)
            call_order.append(index)
        assert index < 2
        started[index].set()
        assert release[index].wait(timeout=10), "migration worker did not receive release"

    monkeypatch.setattr(migrate, "_upgrade_head", controlled_upgrade)
    first = asyncio.create_task(migrate.upgrade_head(database_url))
    second: asyncio.Task[None] | None = None

    async def lock_state() -> tuple[int, int]:
        async with observer.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT count(*) FILTER (WHERE granted), "
                        "count(*) FILTER (WHERE NOT granted) "
                        "FROM pg_locks "
                        "WHERE locktype = 'advisory' "
                        "AND database = (SELECT oid FROM pg_database "
                        "WHERE datname = current_database()) "
                        "AND classid = :classid AND objid = :objid AND objsubid = 1"
                    ),
                    {"classid": classid, "objid": objid},
                )
            ).one()
        return int(row[0]), int(row[1])

    try:
        assert await asyncio.to_thread(started[0].wait, 5), "first migration worker did not start"
        second = asyncio.create_task(migrate.upgrade_head(database_url))

        deadline = asyncio.get_running_loop().time() + 5
        while True:
            granted, queued = await lock_state()
            if granted == 1 and queued == 1:
                break
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail(f"expected one granted and one waiting lock, got {granted}/{queued}")
            await asyncio.sleep(0.05)

        assert not started[1].is_set(), "second migration worker ran before lock release"
        release[0].set()
        assert await asyncio.to_thread(started[1].wait, 5), "second migration worker did not start"
        release[1].set()
        await asyncio.gather(first, second)
        assert call_order == [0, 1]
    finally:
        release[0].set()
        release[1].set()
        tasks = [task for task in (first, second) if task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await observer.dispose()
