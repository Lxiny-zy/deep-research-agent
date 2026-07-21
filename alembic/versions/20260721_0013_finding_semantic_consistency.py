"""Persist semantic evidence and consistency verification metadata.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "finding",
        sa.Column("semantic_status", sa.String(16), nullable=False, server_default="not_checked"),
    )
    op.add_column(
        "finding",
        sa.Column("semantic_confidence", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "finding",
        sa.Column("semantic_reason", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "finding",
        sa.Column("claim_id", sa.String(32), nullable=False, server_default=""),
    )
    op.add_column(
        "finding",
        sa.Column(
            "consistency_status",
            sa.String(16),
            nullable=False,
            server_default="not_checked",
        ),
    )
    op.add_column(
        "finding",
        sa.Column("contradicts_claim_ids", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "finding",
        sa.Column("contradiction_reason", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("finding", "contradiction_reason")
    op.drop_column("finding", "contradicts_claim_ids")
    op.drop_column("finding", "consistency_status")
    op.drop_column("finding", "claim_id")
    op.drop_column("finding", "semantic_reason")
    op.drop_column("finding", "semantic_confidence")
    op.drop_column("finding", "semantic_status")
