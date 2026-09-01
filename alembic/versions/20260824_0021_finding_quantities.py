"""Persist structured quantities, experiment conditions, and numeric verification.

Scientific claims are not sentences but "under these conditions, this metric equals
this value".  Modelling the number separately from the prose is what lets a
comparison table be built without re-parsing free text — re-parsing would move the
fabrication risk into the report layer, which is exactly what the evidence gate
exists to prevent.

``quantity`` and ``conditions`` are JSON columns: each arrives from one extraction
and is consumed as a unit, nothing queries them field-by-field, and splitting them
into a dozen typed columns would make every new condition field cost a migration.

``quantity_status`` is three-valued and defaults to ``not_applicable`` so existing
qualitative findings are unaffected: only a claim that *asserts* a number and fails
the deterministic check is barred from the report.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("finding")}
    if "quantity_status" not in existing:
        op.add_column(
            "finding",
            sa.Column(
                "quantity_status",
                sa.String(length=16),
                nullable=False,
                server_default="not_applicable",
            ),
        )
    if "quantity_reason" not in existing:
        op.add_column(
            "finding",
            sa.Column("quantity_reason", sa.Text(), nullable=False, server_default=""),
        )
    if "quantity" not in existing:
        op.add_column("finding", sa.Column("quantity", sa.JSON(), nullable=True))
    if "conditions" not in existing:
        op.add_column("finding", sa.Column("conditions", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("finding", "conditions")
    op.drop_column("finding", "quantity")
    op.drop_column("finding", "quantity_reason")
    op.drop_column("finding", "quantity_status")
