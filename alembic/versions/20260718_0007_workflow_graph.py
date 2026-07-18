"""Add versioned graph fields to workflow definitions.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_def", sa.Column("nodes", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "workflow_def", sa.Column("edges", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "workflow_def", sa.Column("viewport", sa.JSON(), nullable=False, server_default="{}")
    )
    op.add_column(
        "workflow_def", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )


def downgrade() -> None:
    op.drop_column("workflow_def", "version")
    op.drop_column("workflow_def", "viewport")
    op.drop_column("workflow_def", "edges")
    op.drop_column("workflow_def", "nodes")
