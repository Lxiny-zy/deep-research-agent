"""Add workflow definition snapshots and blackboard checkpoints.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_run", sa.Column("definition", sa.JSON(), nullable=False, server_default="{}")
    )
    op.add_column(
        "workflow_run", sa.Column("checkpoint", sa.JSON(), nullable=False, server_default="{}")
    )


def downgrade() -> None:
    op.drop_column("workflow_run", "checkpoint")
    op.drop_column("workflow_run", "definition")
