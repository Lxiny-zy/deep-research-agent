"""Persist deterministic evidence verification metadata.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "finding",
        sa.Column("evidence_quote", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "finding",
        sa.Column(
            "verification_status", sa.String(16), nullable=False, server_default="unverified"
        ),
    )
    op.add_column(
        "finding",
        sa.Column("verification_method", sa.String(32), nullable=False, server_default="none"),
    )
    op.add_column(
        "finding",
        sa.Column("source_content_hash", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "finding",
        sa.Column("verification_reason", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("finding", "verification_reason")
    op.drop_column("finding", "source_content_hash")
    op.drop_column("finding", "verification_method")
    op.drop_column("finding", "verification_status")
    op.drop_column("finding", "evidence_quote")
