"""Persist the first cancellation transition time without guessing legacy ages."""

import sqlalchemy as sa

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy create_all databases may already include this nullable field.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("research_run")}
    if "cancel_requested_at" not in columns:
        op.add_column(
            "research_run",
            sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("research_run", "cancel_requested_at")
