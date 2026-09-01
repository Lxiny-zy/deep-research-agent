"""Add the worker claim queue columns.

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-20

``execution_mode=worker`` needs to distinguish a run that has been enqueued but
never started from a run whose executor crashed before writing its first
checkpoint.  Both look identical today (``status='pending'`` with an empty
checkpoint), and the recovery scan resolves that ambiguity by failing the run.

``claimable_at`` makes the queued state explicit:

* ``status='pending'`` and ``claimable_at`` set → waiting for a worker.
* ``status='running'`` with an expired lease    → crashed, resume from checkpoint.
* ``status='pending'`` and ``claimable_at`` NULL → inline execution, or a crash
  before the first checkpoint; the pre-existing recovery rules still apply.

``claim_attempts`` bounds how often a single run may be handed between workers
so a deterministically crashing run cannot loop forever.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _columns("research_run")
    with op.batch_alter_table("research_run") as batch:
        if "claimable_at" not in columns:
            batch.add_column(sa.Column("claimable_at", sa.DateTime(timezone=True), nullable=True))
        if "claim_attempts" not in columns:
            batch.add_column(
                sa.Column("claim_attempts", sa.Integer(), nullable=False, server_default="0")
            )

    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("research_run")}
    if "ix_research_run_claimable" not in indexes:
        # The worker polls (status, claimable_at); a composite index keeps that
        # query from scanning the full history table as runs accumulate.
        op.create_index(
            "ix_research_run_claimable",
            "research_run",
            ["status", "claimable_at"],
        )


def downgrade() -> None:
    op.drop_index("ix_research_run_claimable", table_name="research_run")
    with op.batch_alter_table("research_run") as batch:
        batch.drop_column("claim_attempts")
        batch.drop_column("claimable_at")
