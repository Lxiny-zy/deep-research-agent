"""0038 preserves legacy checkpoints and makes only recoverable work claimable."""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from alembic import command
from tests.test_migrations_pg import _alembic_config


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
