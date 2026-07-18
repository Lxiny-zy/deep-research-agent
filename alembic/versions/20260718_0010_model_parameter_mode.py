"""Add model sampling/reasoning parameter modes.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_profile",
        sa.Column("parameter_mode", sa.String(16), nullable=False, server_default="temperature"),
    )
    op.add_column(
        "model_profile",
        sa.Column("reasoning_effort", sa.String(16), nullable=False, server_default="medium"),
    )


def downgrade() -> None:
    op.drop_column("model_profile", "reasoning_effort")
    op.drop_column("model_profile", "parameter_mode")
