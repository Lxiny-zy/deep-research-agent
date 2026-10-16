"""Keep research admission accounting after runs are deleted."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "usage_ledger" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        # Deliberately no FK: a deleted run must not erase its admission record.
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("owner_id", "run_id", "event_type", name="uq_usage_ledger_event"),
    )
    op.create_index("ix_usage_ledger_owner_id", "usage_ledger", ["owner_id"])
    op.create_index("ix_usage_ledger_owner_created", "usage_ledger", ["owner_id", "created_at"])
    op.create_index("ix_usage_ledger_run_id", "usage_ledger", ["run_id"])
    op.execute(
        sa.text(
            "INSERT INTO usage_ledger (id,owner_id,run_id,event_type,created_at,tokens) "
            "SELECT id,owner_id,id,'run_admitted',created_at,COALESCE(total_tokens,0) "
            "FROM research_run WHERE owner_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    if "usage_ledger" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("usage_ledger")
