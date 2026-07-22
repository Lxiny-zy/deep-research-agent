"""数据库引擎与会话工厂（async SQLAlchemy 2.0）。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .orm import Base

_SQLITE_JOURNAL_MODES = {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}
_ALEMBIC_HEAD = "0013"
_LEGACY_FINDING_COLUMN_REPAIRS: tuple[tuple[str, str], ...] = (
    ("evidence_quote", "ALTER TABLE finding ADD COLUMN evidence_quote TEXT NOT NULL DEFAULT ''"),
    (
        "verification_status",
        "ALTER TABLE finding ADD COLUMN verification_status VARCHAR(16) "
        "NOT NULL DEFAULT 'unverified'",
    ),
    (
        "verification_method",
        "ALTER TABLE finding ADD COLUMN verification_method VARCHAR(32) NOT NULL DEFAULT 'none'",
    ),
    (
        "source_content_hash",
        "ALTER TABLE finding ADD COLUMN source_content_hash VARCHAR(64) NOT NULL DEFAULT ''",
    ),
    (
        "verification_reason",
        "ALTER TABLE finding ADD COLUMN verification_reason TEXT NOT NULL DEFAULT ''",
    ),
    (
        "semantic_status",
        "ALTER TABLE finding ADD COLUMN semantic_status VARCHAR(16) NOT NULL DEFAULT 'not_checked'",
    ),
    (
        "semantic_confidence",
        "ALTER TABLE finding ADD COLUMN semantic_confidence FLOAT NOT NULL DEFAULT 0",
    ),
    ("semantic_reason", "ALTER TABLE finding ADD COLUMN semantic_reason TEXT NOT NULL DEFAULT ''"),
    ("claim_id", "ALTER TABLE finding ADD COLUMN claim_id VARCHAR(32) NOT NULL DEFAULT ''"),
    (
        "consistency_status",
        "ALTER TABLE finding ADD COLUMN consistency_status VARCHAR(16) "
        "NOT NULL DEFAULT 'not_checked'",
    ),
    (
        "contradicts_claim_ids",
        "ALTER TABLE finding ADD COLUMN contradicts_claim_ids JSON NOT NULL DEFAULT '[]'",
    ),
    (
        "contradiction_reason",
        "ALTER TABLE finding ADD COLUMN contradiction_reason TEXT NOT NULL DEFAULT ''",
    ),
)


def make_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """创建 async 引擎。database_url 形如 postgresql+asyncpg://... 或 sqlite+aiosqlite://..."""
    engine = create_async_engine(database_url, echo=echo, pool_pre_ping=True)
    if database_url.startswith("sqlite"):
        # SQLite 单写者模型：WAL 允许读写并发、加长 busy_timeout，
        # 避免多个 run 并发落库时直接抛 "database is locked"；
        # 并开启外键强制（默认关闭），使 ondelete=CASCADE 在删除 run 时真正级联清子表
        @sa_event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragma(dbapi_conn: Any, _record: Any) -> None:
            cursor = dbapi_conn.cursor()
            journal_mode = os.getenv("SQLITE_JOURNAL_MODE", "WAL").strip().upper()
            if journal_mode not in _SQLITE_JOURNAL_MODES:
                journal_mode = "WAL"
            cursor.execute(f"PRAGMA journal_mode={journal_mode}")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    """直接建表（测试 / 无 Alembic 的快速启动用；生产用 alembic upgrade head）。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def prepare_sqlite_schema(engine: AsyncEngine, database_url: str) -> None:
    """Prepare local SQLite startup without leaving old schemas half-upgraded.

    Fresh and Alembic-managed databases use Alembic. Legacy zero-config SQLite
    databases created by ``create_all`` have no ``alembic_version`` table, so we
    repair the columns introduced after that path and stamp the DB at head.
    """
    if not database_url.startswith("sqlite"):
        return
    if ":memory:" in database_url:
        await create_all(engine)
        return

    tables = await _sqlite_table_names(engine)
    if "alembic_version" in tables or not tables:
        await engine.dispose()
        await _run_alembic_upgrade(database_url)
        return

    await create_all(engine)
    await _repair_legacy_sqlite_schema(engine)
    await _stamp_sqlite_head(engine)


async def _sqlite_table_names(engine: AsyncEngine) -> set[str]:
    async with engine.begin() as conn:
        names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    return {name for name in names if not name.startswith("sqlite_")}


async def _repair_legacy_sqlite_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"] for column in inspect(sync_conn).get_columns("finding")
            }
            if inspect(sync_conn).has_table("finding")
            else set()
        )
        for column_name, ddl in _LEGACY_FINDING_COLUMN_REPAIRS:
            if column_name not in columns:
                await conn.execute(text(ddl))
                columns.add(column_name)


async def _stamp_sqlite_head(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        versions = [row[0] for row in result.fetchall()]
        if versions:
            await conn.execute(text("DELETE FROM alembic_version"))
        await conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:version)"),
            {"version": _ALEMBIC_HEAD},
        )


async def _run_alembic_upgrade(database_url: str) -> None:
    def _upgrade() -> None:
        from alembic.config import Config

        from alembic import command

        project_root = Path(__file__).resolve().parents[2]
        config = Config(str(project_root / "alembic.ini"))
        config.set_main_option("script_location", str(project_root / "alembic"))
        previous_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = database_url
        try:
            command.upgrade(config, "head")
        finally:
            if previous_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_url

    await asyncio.to_thread(_upgrade)
