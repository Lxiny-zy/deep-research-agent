"""Add the search provider to each catalog key.

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def _has_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(column["name"] == "provider" for column in inspector.get_columns("search_key"))


def upgrade() -> None:
    if not _has_column():
        op.add_column(
            "search_key",
            sa.Column("provider", sa.String(length=16), nullable=False, server_default="tavily"),
        )
        op.create_index("ix_search_key_provider_priority", "search_key", ["provider", "priority"])


def downgrade() -> None:
    if _has_column():
        op.drop_index("ix_search_key_provider_priority", table_name="search_key")
        op.drop_column("search_key", "provider")
