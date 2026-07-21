"""Align catalog and workflow timestamps with ORM nullability.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMP_COLUMNS = (
    ("model_profile", "created_at"),
    ("agent_card", "created_at"),
    ("search_key", "created_at"),
    ("workflow_def", "created_at"),
    ("workflow_def", "updated_at"),
)


def upgrade() -> None:
    # Older schemas allowed NULL despite the ORM's non-optional datetime type.
    # Backfill first so tightening the constraint is safe for existing rows.
    for table_name, column_name in _TIMESTAMP_COLUMNS:
        table = sa.table(table_name, sa.column(column_name, sa.DateTime(timezone=True)))
        op.execute(
            table.update()
            .where(table.c[column_name].is_(None))
            .values({column_name: sa.func.now()})
        )
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                column_name,
                existing_type=sa.DateTime(timezone=True),
                existing_server_default=sa.func.now(),
                nullable=False,
            )


def downgrade() -> None:
    for table_name, column_name in reversed(_TIMESTAMP_COLUMNS):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                column_name,
                existing_type=sa.DateTime(timezone=True),
                existing_server_default=sa.func.now(),
                nullable=True,
            )
