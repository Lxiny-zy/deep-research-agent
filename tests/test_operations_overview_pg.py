"""Operational JSON projections must agree on PostgreSQL and SQLite."""

from datetime import UTC, datetime, timedelta

import pytest

from deep_research.persistence.db import make_engine, make_sessionmaker
from deep_research.workbench.operations_source import sql_snapshot
from deep_research.workbench.operations_summary import summarize
from tests.test_migrations_pg import _isolated_database, _postgres_url, _run_migration
from tests.test_operations_overview import seed


@pytest.mark.pg
async def test_operational_json_projection_on_postgres():
    async with _isolated_database(_postgres_url()) as url:
        await _run_migration(url, "head")
        engine = make_engine(url)
        try:
            sessions = make_sessionmaker(engine)
            await seed(sessions)
            until = datetime.now(UTC)
            since = until - timedelta(days=7)
            data = await sql_snapshot(sessions, owner="alice", since=since, until=until)
            result = summarize(data, since=since, until=until)
            assert result["coverage"]["research_records"] == 2
            assert result["coverage"]["qa_records"] == 1
            assert result["model_calls"]["recorded_attempts"] == 2
            assert result["model_calls"]["statuses"] == {"succeeded": 1, "failed": 1}
            assert result["model_calls"]["usage"]["total_tokens"]["known_total"] == 12
        finally:
            await engine.dispose()
