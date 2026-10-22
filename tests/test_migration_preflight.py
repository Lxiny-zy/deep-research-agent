"""Maintenance is opt-in, atomic and preserves original ownership/material."""

import pytest
import sqlalchemy as sa

from deep_research.migration_preflight import preflight


def legacy_database(tmp_path):
    path = tmp_path / "legacy.db"
    engine = sa.create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE research_run (id TEXT PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE coordination (name TEXT PRIMARY KEY)"))
        conn.execute(sa.text(
            "CREATE TABLE qa_conversation "
            "(id TEXT PRIMARY KEY, owner_id TEXT, run_id TEXT, title TEXT)"
        ))
        conn.execute(sa.text(
            "CREATE TABLE qa_message "
            "(id TEXT PRIMARY KEY, conversation_id TEXT, status TEXT, answer TEXT)"
        ))
        conn.execute(sa.text(
            "INSERT INTO qa_conversation VALUES ('orphan','alice','deleted-task','private')"
        ))
        conn.execute(sa.text(
            "INSERT INTO qa_message VALUES ('m','orphan','done','original answer')"
        ))
    return engine, f"sqlite+aiosqlite:///{path.as_posix()}"


async def test_check_is_read_only_and_preserve_keeps_snapshot(tmp_path):
    engine, url = legacy_database(tmp_path)
    try:
        result = await preflight(url)
        assert result["orphan_count"] == 1
        assert "qa_orphan_archive" not in sa.inspect(engine).get_table_names()
        await preflight(url, conversation_ids=["orphan"])
        assert (await preflight(url))["orphan_count"] == 0
        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT owner_id,run_id FROM qa_conversation")).one()
            assert tuple(row) == ("alice", None)
            assert conn.scalar(sa.text("SELECT answer FROM qa_message")) == "original answer"
            archive = sa.Table("qa_orphan_archive", sa.MetaData(), autoload_with=conn)
            saved = conn.execute(sa.select(archive)).mappings().one()
            assert saved["original_run_id"] == "deleted-task"
            assert saved["snapshot"]["conversation"]["owner_id"] == "alice"
            assert saved["snapshot"]["messages"][0]["answer"] == "original answer"
    finally:
        engine.dispose()


async def test_missing_or_active_selection_does_not_partially_detach(tmp_path):
    engine, url = legacy_database(tmp_path)
    try:
        with pytest.raises(ValueError):
            await preflight(url, conversation_ids=["orphan", "missing"])
        with engine.begin() as conn:
            assert conn.scalar(sa.text("SELECT run_id FROM qa_conversation")) == "deleted-task"
            conn.execute(sa.text("UPDATE qa_message SET status='running'"))
        with pytest.raises(ValueError):
            await preflight(url, conversation_ids=["orphan"])
        with engine.connect() as conn:
            assert conn.scalar(sa.text("SELECT run_id FROM qa_conversation")) == "deleted-task"
    finally:
        engine.dispose()
