"""Persist identity scheduling credit and immutable task scheduling estimates.

Revision ID: 0038
Revises: 0037
"""

import sqlalchemy as sa

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("research_run")}
    # Existing work is conservatively heavy. This preserves its checkpoint,
    # owner and lease while preventing an unknown long task taking the light slot.
    additions = [
        sa.Column(
            "schedule_cost",
            sa.Integer(),
            sa.CheckConstraint(
                "schedule_cost BETWEEN 1 AND 8", name="ck_research_run_schedule_cost"
            ),
            nullable=False,
            server_default="4",
        ),
        sa.Column(
            "schedule_class",
            sa.String(8),
            sa.CheckConstraint(
                "schedule_class IN ('light', 'heavy')", name="ck_research_run_schedule_class"
            ),
            nullable=False,
            server_default="heavy",
        ),
        sa.Column(
            "schedule_priority",
            sa.Integer(),
            sa.CheckConstraint(
                "schedule_priority BETWEEN 0 AND 2", name="ck_research_run_schedule_priority"
            ),
            nullable=False,
            server_default="1",
        ),
    ]
    for column in additions:
        if column.name not in columns:
            op.add_column("research_run", column)
    runs = sa.table(
        "research_run",
        sa.column("id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("claimable_at", sa.DateTime(timezone=True)),
    )
    workflows = sa.table(
        "workflow_run",
        sa.column("research_run_id", sa.String()),
        sa.column("checkpoint", sa.JSON()),
    )
    # Old inline tasks did not enter the worker queue. Make only checkpointed
    # unfinished work discoverable; an unexpired lease still prevents takeover.
    recoverable = [
        row.id
        for row in bind.execute(
            sa.select(runs.c.id, workflows.c.checkpoint)
            .join(workflows, workflows.c.research_run_id == runs.c.id)
            .where(runs.c.status.in_(("pending", "running")), runs.c.claimable_at.is_(None))
        )
        if isinstance(row.checkpoint, dict) and row.checkpoint
    ]
    for offset in range(0, len(recoverable), 500):
        bind.execute(
            runs.update()
            .where(runs.c.id.in_(recoverable[offset : offset + 500]))
            .values(claimable_at=sa.func.current_timestamp())
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("research_run")}
    if "ix_research_run_schedule_identity" not in indexes:
        op.create_index(
            "ix_research_run_schedule_identity",
            "research_run",
            ["owner_id", "status", "claimable_at"],
        )
    tables = set(sa.inspect(bind).get_table_names())
    if "scheduler_state" not in tables:
        op.create_table(
            "scheduler_state",
            sa.Column("name", sa.String(32), primary_key=True),
            sa.Column("cursor", sa.String(80), nullable=False, server_default=""),
            sa.Column("round_no", sa.BigInteger(), nullable=False, server_default="1"),
        )
    if "scheduler_identity" not in tables:
        op.create_table(
            "scheduler_identity",
            sa.Column("identity_key", sa.String(80), primary_key=True),
            sa.Column("deficit", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_round", sa.BigInteger(), nullable=False, server_default="0"),
            sa.CheckConstraint(
                "deficit >= 0 AND deficit <= 8", name="ck_scheduler_identity_deficit"
            ),
        )


def downgrade() -> None:
    op.drop_table("scheduler_identity")
    op.drop_table("scheduler_state")
    op.drop_index("ix_research_run_schedule_identity", table_name="research_run")
    constraints = {
        check["name"] for check in sa.inspect(op.get_bind()).get_check_constraints("research_run")
    }
    with op.batch_alter_table("research_run") as batch:
        for suffix in ("cost", "class", "priority"):
            name = f"ck_research_run_schedule_{suffix}"
            if name in constraints:
                batch.drop_constraint(name, type_="check")
            batch.drop_column(f"schedule_{suffix}")
