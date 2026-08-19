"""数据库引擎与会话工厂（async SQLAlchemy 2.0）。"""

from __future__ import annotations

import asyncio
import os
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy import inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .orm import Base

_SQLITE_JOURNAL_MODES = {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}


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
    databases created by ``create_all`` have no ``alembic_version`` table. They
    are temporarily stamped at the last pre-reconciliation revision and then
    passed through the idempotent reconciliation migration. This is important:
    ``create_all`` does not add columns to tables that already exist.
    """
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return
    if _is_sqlite_memory_url(url):
        await create_all(engine)
        return

    tables = await _sqlite_table_names(engine)
    if "alembic_version" in tables or not tables:
        await engine.dispose()
        await _run_alembic_upgrade(database_url)
        return

    # A legacy create_all database has no trustworthy revision. The
    # reconciliation migration is deliberately written to repair any schema
    # shape that could have been produced by older create_all versions.
    await _stamp_sqlite_revision(engine, "0013")
    await engine.dispose()
    await _run_alembic_upgrade(database_url)


def _is_sqlite_memory_url(url: URL) -> bool:
    database = url.database
    if database is None or database in {"", ":memory:"}:
        return True

    # SQLite only interprets ``mode=memory`` as a URI filename option when
    # SQLAlchemy's SQLite URI mode is enabled explicitly.
    uri_enabled = str(url.query.get("uri", "")).casefold() in {
        "1",
        "on",
        "true",
        "yes",
    }
    if not uri_enabled:
        return False

    mode = str(url.query.get("mode", "")).casefold()
    return mode == "memory" or database.casefold() == "file::memory:"


async def _sqlite_table_names(engine: AsyncEngine) -> set[str]:
    async with engine.begin() as conn:
        names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    return {name for name in names if not name.startswith("sqlite_")}


def _migration_root() -> Path:
    """Locate Alembic assets in a source checkout or an installed wheel."""
    source_root = Path(__file__).resolve().parents[2]
    candidates = [source_root, Path(sys.prefix) / "share" / "deep-research-agent"]
    try:
        installed = distribution("deep-research-agent")
    except PackageNotFoundError:
        installed = None
    if installed is not None:
        for entry in installed.files or ():
            if entry.as_posix().endswith("share/deep-research-agent/alembic.ini"):
                candidates.append(Path(str(installed.locate_file(entry))).parent)
                break
    for root in candidates:
        if (root / "alembic.ini").is_file() and (root / "alembic" / "versions").is_dir():
            return root
    raise RuntimeError(
        "Alembic 迁移资源未找到；请从源码树运行，或安装包含 share/deep-research-agent 的完整 wheel"
    )


async def _stamp_sqlite_revision(engine: AsyncEngine, revision: str) -> None:
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
            {"version": revision},
        )


async def _run_alembic_upgrade(database_url: str) -> None:
    def _upgrade() -> None:
        from alembic.config import Config

        from alembic import command

        project_root = _migration_root()
        config = Config(str(project_root / "alembic.ini"))
        config.set_main_option("script_location", str(project_root / "alembic"))
        # 经 Config.attributes 显式传连接串给 env.py，不临时改写进程级
        # DATABASE_URL（那会与并发读 os.environ 的代码产生隐式全局耦合）
        config.attributes["database_url"] = database_url
        command.upgrade(config, "head")

    await asyncio.to_thread(_upgrade)
