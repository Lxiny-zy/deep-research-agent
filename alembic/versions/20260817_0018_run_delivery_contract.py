"""Add production run delivery and event replay fields.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    research_columns = _columns("research_run")
    with op.batch_alter_table("research_run") as batch:
        if "idempotency_key" not in research_columns:
            batch.add_column(sa.Column("idempotency_key", sa.String(128), nullable=True))
        if "request_hash" not in research_columns:
            batch.add_column(sa.Column("request_hash", sa.String(64), nullable=True))

    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("research_run")}
    if "uq_research_run_idempotency_key" not in indexes:
        op.create_index(
            "uq_research_run_idempotency_key",
            "research_run",
            ["idempotency_key"],
            unique=True,
        )

    event_columns = _columns("event")
    if "attempt" not in event_columns:
        with op.batch_alter_table("event") as batch:
            batch.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))

    workflow_columns = _columns("workflow_run")
    if "attempt" not in workflow_columns:
        with op.batch_alter_table("workflow_run") as batch:
            batch.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    with op.batch_alter_table("workflow_run") as batch:
        batch.drop_column("attempt")
    with op.batch_alter_table("event") as batch:
        batch.drop_column("attempt")
    op.drop_index("uq_research_run_idempotency_key", table_name="research_run")
    with op.batch_alter_table("research_run") as batch:
        batch.drop_column("request_hash")
        batch.drop_column("idempotency_key")
