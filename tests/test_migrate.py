from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text

from deep_research import migrate
from deep_research.persistence.db import make_engine


def _alembic_head() -> str:
    """Resolve the migration head rather than pinning a revision in the test."""
    return ScriptDirectory.from_config(AlembicConfig("alembic.ini")).get_current_head()


async def test_upgrade_head_initializes_sqlite(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}"
    await migrate.upgrade_head(database_url)

    engine = make_engine(database_url)
    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert revision == _alembic_head()
    finally:
        await engine.dispose()


async def test_search_resource_upgrade_preserves_legacy_keys_and_prompt_mode(tmp_path) -> None:
    from alembic import command

    url = f"sqlite+aiosqlite:///{tmp_path / 'legacy-search.db'}"
    config = AlembicConfig("alembic.ini")
    config.attributes["database_url"] = url
    await asyncio.to_thread(command.upgrade, config, "0023")
    engine = make_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO search_key (id, api_key, priority) "
                    "VALUES ('legacy-key', 'preserve-this-secret', 7)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO agent_card (id, name, behavior, system_prompt) "
                    "VALUES ('legacy-role', 'legacy', 'research', 'original prompt')"
                )
            )
    finally:
        await engine.dispose()
    await migrate.upgrade_head(url)
    engine = make_engine(url)
    try:
        async with engine.connect() as connection:
            key = (
                await connection.execute(
                    text(
                        "SELECT api_key, provider, priority FROM search_key WHERE id = 'legacy-key'"
                    )
                )
            ).one()
            role = (
                await connection.execute(
                    text(
                        "SELECT system_prompt, prompt_mode, search_profile_ids "
                        "FROM agent_card WHERE id = 'legacy-role'"
                    )
                )
            ).one()
        assert tuple(key) == ("preserve-this-secret", "tavily", 7)
        assert tuple(role) == ("original prompt", "replace", None)
    finally:
        await engine.dispose()


class _FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.commits = 0

    async def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> None:
        self.calls.append((str(statement), parameters))

    async def commit(self) -> None:
        self.commits += 1


class _FakeConnectionContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeEngine:
    def __init__(self) -> None:
        self.connection = _FakeConnection()
        self.disposed = False

    def connect(self) -> _FakeConnectionContext:
        return _FakeConnectionContext(self.connection)

    async def dispose(self) -> None:
        self.disposed = True


async def test_upgrade_head_serializes_postgres_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _FakeEngine()
    upgraded: list[str] = []

    monkeypatch.setattr(migrate, "make_engine", lambda _url: engine)
    monkeypatch.setattr(migrate, "_upgrade_head", upgraded.append)

    database_url = "postgresql+asyncpg://dr:password@db/deep_research"
    await migrate.upgrade_head(database_url)

    assert upgraded == [database_url]
    assert engine.disposed
    assert engine.connection.commits == 2
    assert "pg_advisory_lock" in engine.connection.calls[0][0]
    assert "pg_advisory_unlock" in engine.connection.calls[1][0]


async def test_upgrade_head_waits_for_cancelled_migration_before_unlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled caller must not release the lock while Alembic is running."""
    engine = _FakeEngine()
    started = threading.Event()
    release = threading.Event()

    def blocking_upgrade(_database_url: str) -> None:
        started.set()
        assert release.wait(timeout=5), "test migration worker did not receive release"

    monkeypatch.setattr(migrate, "make_engine", lambda _url: engine)
    monkeypatch.setattr(migrate, "_upgrade_head", blocking_upgrade)

    database_url = "postgresql+asyncpg://dr:password@db/deep_research"
    migration = asyncio.create_task(migrate.upgrade_head(database_url))
    assert await asyncio.to_thread(started.wait, 2), "migration worker did not start"

    migration.cancel()
    await asyncio.sleep(0)
    assert not any("pg_advisory_unlock" in statement for statement, _ in engine.connection.calls)

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await migration

    assert any("pg_advisory_unlock" in statement for statement, _ in engine.connection.calls)
    assert engine.disposed


async def test_migration_does_not_disable_application_logging(tmp_path) -> None:
    """进程内迁移不得让应用日志失声。

    alembic/env.py 的 fileConfig 默认 disable_existing_loggers=True，会禁用此刻
    已存在的所有 logger。API 启动时就在进程内跑迁移，一旦回归，之后所有
    deep_research.* 日志都会消失，且没有任何报错提示。
    """
    logger = logging.getLogger("deep_research.execution")
    assert not logger.disabled

    await migrate.upgrade_head(f"sqlite+aiosqlite:///{tmp_path / 'logging.db'}")

    assert not logger.disabled, "迁移之后应用 logger 被禁用了"
