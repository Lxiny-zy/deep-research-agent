"""Cascade task deletion to task-bound Q&A conversations."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "qa_conversation" not in tables or "research_run" not in tables:
        return

    # Do not silently reclassify private task discussions as standalone chats.
    # An operator must explicitly resolve historical orphans before migration.
    orphans = bind.scalar(
        sa.text(
            "SELECT count(*) FROM qa_conversation WHERE run_id IS NOT NULL "
            "AND run_id NOT IN (SELECT id FROM research_run)"
        )
    )
    if orphans:
        raise RuntimeError(
            f"Task-bound Q&A contains {orphans} orphan bindings. "
            "Set DATABASE_URL and run: python -m deep_research.migration_preflight check. "
            "To preserve selected chats, stop services and use the preserve command "
            "with --conversation-id ID --services-stopped --apply, then retry migration."
        )
    existing = {
        fk.get("name") for fk in inspector.get_foreign_keys("qa_conversation") if fk.get("name")
    }
    if any(
        fk.get("referred_table") == "research_run" and fk.get("constrained_columns") == ["run_id"]
        for fk in inspector.get_foreign_keys("qa_conversation")
    ):
        return
    name = "fk_qa_conversation_run_id"
    if name in existing:
        name = "fk_qa_conversation_run_id_cascade"
    if bind.dialect.name == "sqlite":
        # Alembic uses its own connection with foreign_keys disabled. Rebuild
        # only there: a rebuild with enforcement enabled could delete messages.
        if bind.scalar(sa.text("PRAGMA foreign_keys")):
            raise RuntimeError(
                "SQLite parent-table migration requires foreign_keys=OFF "
                "on the migration connection"
            )
    with op.batch_alter_table("qa_conversation") as batch:
        batch.create_foreign_key(name, "research_run", ["run_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    if "qa_conversation" not in sa.inspect(op.get_bind()).get_table_names():
        return
    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and bind.scalar(sa.text("PRAGMA foreign_keys")):
        raise RuntimeError(
            "SQLite parent-table migration requires foreign_keys=OFF "
            "on the migration connection"
        )
    with op.batch_alter_table("qa_conversation") as batch:
        for fk in sa.inspect(bind).get_foreign_keys("qa_conversation"):
            if fk.get("referred_table") == "research_run" and fk.get("name"):
                batch.drop_constraint(fk["name"], type_="foreignkey")
