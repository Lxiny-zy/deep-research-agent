"""Add recovery lease fields for multi-instance coordination.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workflow_run", sa.Column("lease_owner", sa.String(64), nullable=True))
    op.add_column(
        "workflow_run", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("workflow_run", "lease_expires_at")
    op.drop_column("workflow_run", "lease_owner")
