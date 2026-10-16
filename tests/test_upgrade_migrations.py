"""Upgrading real historical rows preserves messages and accounting."""

import sqlalchemy as sa

from alembic import command
from tests.test_migrations_pg import _alembic_config


def test_upgrade_roundtrip_preserves_bound_messages_and_backfills_usage(tmp_path):
    path = tmp_path / "migration.db"
    config = _alembic_config(f"sqlite+aiosqlite:///{path.as_posix()}")
    command.upgrade(config, "0041")
    engine = sa.create_engine(f"sqlite:///{path.as_posix()}")
    try:
        with engine.begin() as c:
            c.execute(
                sa.text(
                    "INSERT INTO research_run "
                    "(id,owner_id,query,status,interpretation,elapsed,total_tokens) "
                    "VALUES ('r','a','q','done','',0,123)"
                )
            )
            c.execute(
                sa.text(
                    "INSERT INTO qa_conversation (id,owner_id,title,run_id) "
                    "VALUES ('c','a','chat','r')"
                )
            )
            c.execute(
                sa.text(
                    "INSERT INTO qa_message "
                    "(id,conversation_id,position,query,answer,citations,evidence,thoughts,status) "
                    "VALUES ('m','c',0,'q','answer','[]','[]','[]','done')"
                )
            )
        for _ in range(2):
            command.upgrade(config, "head")
            with engine.connect() as c:
                assert c.scalar(sa.text("SELECT answer FROM qa_message WHERE id='m'")) == "answer"
                assert c.scalar(sa.text("SELECT tokens FROM usage_ledger WHERE run_id='r'")) == 123
                assert c.scalar(sa.text("SELECT count(*) FROM usage_ledger")) == 1
                assert not c.execute(sa.text("PRAGMA foreign_key_check")).all()
            command.check(config)
            command.downgrade(config, "0041")
            with engine.connect() as c:
                assert c.scalar(sa.text("SELECT answer FROM qa_message WHERE id='m'")) == "answer"
        command.upgrade(config, "head")
        with engine.connect() as c:
            c.execute(sa.text("PRAGMA foreign_keys=ON"))
            c.commit()
            c.execute(sa.text("DELETE FROM research_run WHERE id='r'"))
            c.commit()
            assert c.scalar(sa.text("SELECT count(*) FROM qa_message")) == 0
            assert c.scalar(sa.text("SELECT count(*) FROM qa_conversation")) == 0
            assert c.scalar(sa.text("SELECT count(*) FROM usage_ledger")) == 1
    finally:
        engine.dispose()
