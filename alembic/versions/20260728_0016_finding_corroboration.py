"""Persist cross-source corroboration metadata for findings.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("finding")}
    if "corroboration_status" not in existing:
        op.add_column(
            "finding",
            sa.Column(
                "corroboration_status",
                sa.String(20),
                nullable=False,
                server_default="not_checked",
            ),
        )
    if "independent_source_count" not in existing:
        op.add_column(
            "finding",
            sa.Column(
                "independent_source_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    if "corroborates_claim_ids" not in existing:
        op.add_column(
            "finding",
            sa.Column(
                "corroborates_claim_ids",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            ),
        )
    if "corroboration_reason" not in existing:
        op.add_column(
            "finding",
            sa.Column("corroboration_reason", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_column("finding", "corroboration_reason")
    op.drop_column("finding", "corroborates_claim_ids")
    op.drop_column("finding", "independent_source_count")
    op.drop_column("finding", "corroboration_status")
