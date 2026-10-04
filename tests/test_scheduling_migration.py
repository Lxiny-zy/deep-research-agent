"""0038 preserves legacy checkpoints and makes only recoverable work claimable."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from alembic import command
from deep_research.persistence.db import make_engine
from tests.test_migrations_pg import (
    _alembic_config,
    _isolated_database,
    _postgres_url,
    _run_migration,
)


@pytest.mark.pg
async def test_postgres_fair_scheduling_migration_roundtrip_preserves_legacy_work():
    async with _isolated_database(_postgres_url()) as url:
        await _run_migration(url, "0037")
        engine = make_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.execute(sa.text(
                    "INSERT INTO research_run "
                    "(id, query, status, interpretation, elapsed, total_tokens) "
                    "VALUES ('legacy', 'legacy', 'running', '', 12, 123)"
                ))
                await connection.execute(sa.text(
                    "INSERT INTO workflow_run "
                    "(id, research_run_id, workflow_name, status, attempt, "
                    "input, output, definition, checkpoint) "
                    "VALUES ('w', 'legacy', 'deep', 'running', 1, "
                    "'{}', '{}', '{}', '{\"query\": \"legacy\"}')"
                ))
            for _ in range(2):
                await _run_migration(url, "0038")
                async with engine.connect() as connection:
                    row = (await connection.execute(sa.text(
                        "SELECT schedule_cost, schedule_class, schedule_priority, "
                        "claimable_at, elapsed, total_tokens FROM research_run WHERE id='legacy'"
                    ))).one()
                    assert tuple(row[:3]) == (4, "heavy", 1)
                    assert row.claimable_at is not None
                    assert (row.elapsed, row.total_tokens) == (12, 123)
                    assert await connection.scalar(sa.text(
                        "SELECT checkpoint FROM workflow_run WHERE id='w'"
                    )) == {"query": "legacy"}
                config = _alembic_config(url)
                await asyncio.to_thread(command.downgrade, config, "0037")
                async with engine.connect() as connection:
                    assert await connection.scalar(sa.text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name='research_run' AND column_name='schedule_cost'"
                    )) == 0
                    assert await connection.scalar(sa.text(
                        "SELECT checkpoint FROM workflow_run WHERE id='w'"
                    )) == {"query": "legacy"}
        finally:
            await engine.dispose()


def test_fair_scheduling_upgrade_backfills_only_checkpointed_unfinished_work(tmp_path):
    path = tmp_path / "legacy.db"
    config = _alembic_config(f"sqlite+aiosqlite:///{path.as_posix()}")
    command.upgrade(config, "0037")
    engine = sa.create_engine(f"sqlite:///{path.as_posix()}")
    try:
        metadata = sa.MetaData()
        runs = sa.Table("research_run", metadata, autoload_with=engine)
        workflows = sa.Table("workflow_run", metadata, autoload_with=engine)
        cases = [
            ("queued", "pending", {"query": "queued"}, None),
            ("live", "running", {"query": "live"}, datetime.now(UTC) + timedelta(hours=1)),
            ("crashed", "running", {"query": "crashed"}, datetime.now(UTC) - timedelta(hours=1)),
            ("empty", "pending", {}, None),
            ("finished", "done", {"query": "finished"}, None),
            ("cancelling", "cancelling", {"query": "cancelling"}, None),
        ]
        with engine.begin() as connection:
            for key, status, checkpoint, lease_expires in cases:
                connection.execute(
                    runs.insert().values(
                        id=key,
                        query=key,
                        status=status,
                        interpretation="",
                        elapsed=12,
                        total_tokens=123,
                        claim_attempts=0,
                    )
                )
                connection.execute(
                    workflows.insert().values(
                        id=f"workflow-{key}",
                        research_run_id=key,
                        workflow_name="deep",
                        status="running",
                        attempt=1,
                        input={"query": key},
                        output={},
                        definition={},
                        checkpoint=checkpoint,
                        lease_owner="old-owner" if lease_expires else None,
                        lease_expires_at=lease_expires,
                    )
                )
            original_workflows = list(connection.execute(sa.select(workflows)).mappings())
        command.upgrade(config, "head")
        migrated = sa.Table("research_run", sa.MetaData(), autoload_with=engine)
        with engine.connect() as connection:
            rows = {row.id: row for row in connection.execute(sa.select(migrated))}
            assert {key for key, row in rows.items() if row.claimable_at is not None} == {
                "queued",
                "live",
                "crashed",
            }
            assert all(
                (row.schedule_cost, row.schedule_class, row.schedule_priority) == (4, "heavy", 1)
                for row in rows.values()
            )
            assert all(row.elapsed == 12 and row.total_tokens == 123 for row in rows.values())
            assert list(connection.execute(sa.select(workflows)).mappings()) == original_workflows
        command.downgrade(config, "0037")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(sa.select(sa.func.count()).select_from(migrated)) == len(cases)
            assert list(connection.execute(sa.select(workflows)).mappings()) == original_workflows
    finally:
        engine.dispose()
