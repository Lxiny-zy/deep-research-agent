"""Persist the retrieval snapshot context used to verify each finding.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("finding")}
    if "source_title" not in existing:
        op.add_column(
            "finding",
            sa.Column("source_title", sa.Text(), nullable=False, server_default=""),
        )
    if "evidence_context" not in existing:
        op.add_column(
            "finding",
            sa.Column("evidence_context", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_column("finding", "evidence_context")
    op.drop_column("finding", "source_title")
