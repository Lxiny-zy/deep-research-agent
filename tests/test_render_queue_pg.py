"""PostgreSQL migration, shared capacity, and fenced result writes."""

import asyncio

import pytest
from sqlalchemy import inspect

from alembic import command
from deep_research.persistence.db import make_engine, make_sessionmaker
from deep_research.persistence.sql_repository import SqlRepository
from deep_research.render_queue import SqlRenderQueue
from tests.test_migrations_pg import (
    _alembic_config,
    _isolated_database,
    _postgres_url,
    _run_migration,
)


@pytest.mark.pg
async def test_postgres_render_queue_survives_migration_and_shares_capacity():
    async with _isolated_database(_postgres_url()) as url:
        await _run_migration(url, "0040")
        await _run_migration(url, "head")
        first_engine, second_engine = make_engine(url), make_engine(url)
        repo = SqlRepository(make_sessionmaker(first_engine))
        first, second = (
            SqlRenderQueue(make_sessionmaker(first_engine)),
            SqlRenderQueue(make_sessionmaker(second_engine)),
        )
        try:
            run_id = await repo.create_run("render queue")
            jobs = [
                await first.reserve(
                    key=f"job-{i}",
                    pool="pg",
                    run_id=run_id,
                    kind="bundle",
                    payload={"i": i},
                    now=100 + i,
                )
                for i in range(4)
            ]
            claims = await asyncio.gather(
                *[
                    (first if i % 2 else second).claim(
                        "pg", f"owner-{i}", now=110, limit=2, lease_seconds=10
                    )
                    for i in range(8)
                ]
            )
            active = [job for job in claims if job is not None]
            assert {job.id for job in active} == {job.id for job in jobs[:2]}
            old = active[0]
            assert not await second.finish(old.id, old.lease_owner, {"bad": True}, now=121)
            resumed = await second.claim("pg", "successor", now=121, limit=2, lease_seconds=10)
            assert resumed is not None and resumed.attempts == 2
            assert await second.finish(resumed.id, "successor", {"version": "saved"}, now=122)
            assert (await first.get(resumed.id)).result == {"version": "saved"}
            assert await repo.delete_run(run_id)
            assert await first.get(resumed.id) is None
        finally:
            await first_engine.dispose()
            await second_engine.dispose()
        await asyncio.to_thread(command.downgrade, _alembic_config(url), "0040")
        check = make_engine(url)
        try:
            async with check.connect() as connection:
                assert "render_job" not in await connection.run_sync(
                    lambda conn: inspect(conn).get_table_names()
                )
        finally:
            await check.dispose()
        await _run_migration(url, "head")
