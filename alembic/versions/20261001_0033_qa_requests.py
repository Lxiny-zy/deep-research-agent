"""Durable, idempotent Q&A turns with execution leases."""

import sqlalchemy as sa

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("qa_message")}
    fields = [
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("request_hash", sa.String(64), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("execution_owner", sa.String(36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
    ]
    for column in fields:
        if column.name not in columns:
            op.add_column("qa_message", column)
    names = {c["name"] for c in sa.inspect(op.get_bind()).get_unique_constraints("qa_message")}
    if "uq_qa_request" not in names:
        with op.batch_alter_table("qa_message") as batch:
            batch.create_unique_constraint("uq_qa_request", ["conversation_id", "request_id"])
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("qa_message")}
    if "ix_qa_request_dispatch" not in indexes:
        op.create_index("ix_qa_request_dispatch", "qa_message", ["status", "created_at"])


def downgrade() -> None:
    with op.batch_alter_table("qa_message") as batch:
        batch.drop_index("ix_qa_request_dispatch")
        batch.drop_constraint("uq_qa_request", type_="unique")
        for name in (
            "request_id",
            "request_hash",
            "request_payload",
            "execution_owner",
            "lease_until",
            "error",
            "tokens",
        ):
            batch.drop_column(name)
