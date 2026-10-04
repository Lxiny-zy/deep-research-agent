import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from alembic import command
from deep_research.persistence.db import make_engine, make_sessionmaker
from deep_research.persistence.sql_repository import SqlRepository
from tests.test_migrations_pg import (
    _alembic_config,
    _isolated_database,
    _postgres_url,
    _run_migration,
)


async def check_roundtrip(url):
    await _run_migration(url, "0038")
    engine = make_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(
                "INSERT INTO research_run "
                "(id, query, status, interpretation, elapsed, total_tokens) "
                "VALUES ('old', 'old cancel', 'cancelling', '', 10, 99)"
            ))
        await _run_migration(url, "head")
        repo = SqlRepository(make_sessionmaker(engine))
        old = await repo.get_run("old")
        assert old.status == "cancelling" and old.cancel_requested_at is None
        assert (old.elapsed, old.total_tokens) == (10, 99)
        run_id = await repo.create_run("new cancel")
        before = datetime.now(UTC)
        assert await asyncio.gather(
            repo.request_cancel(run_id), repo.request_cancel(run_id)
        ) == ["cancelling", "cancelling"]
        requested = (await repo.get_run(run_id)).cancel_requested_at
        assert before <= requested <= datetime.now(UTC)
        await engine.dispose()
        # A fresh pool models another API process/restart reading the same database.
        repo = SqlRepository(make_sessionmaker(engine))
        assert (await repo.get_run(run_id)).cancel_requested_at == requested
        assert await repo.request_cancel(run_id) == "cancelling"
        assert (await repo.get_run(run_id)).cancel_requested_at == requested
        await engine.dispose()
        await asyncio.to_thread(command.downgrade, _alembic_config(url), "0038")
        async with engine.connect() as connection:
            row = (await connection.execute(text(
                "SELECT status, elapsed, total_tokens FROM research_run WHERE id='old'"
            ))).one()
            assert tuple(row) == ("cancelling", 10, 99)
        await engine.dispose()
        await _run_migration(url, "head")
        assert (await repo.get_run(run_id)).cancel_requested_at is None
    finally:
        await engine.dispose()


async def test_sqlite_cancellation_migration(tmp_path):
    await check_roundtrip(f"sqlite+aiosqlite:///{(tmp_path / 'cancel-migration.db').as_posix()}")


@pytest.mark.pg
async def test_postgres_cancellation_migration():
    async with _isolated_database(_postgres_url()) as url:
        await check_roundtrip(url)
