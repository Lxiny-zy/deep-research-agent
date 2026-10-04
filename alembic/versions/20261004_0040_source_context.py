"""Persist parser section bounds and full-document coverage manifests."""

import sqlalchemy as sa

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("source")}
    if "source_context" not in columns:
        op.add_column(
            "source", sa.Column("source_context", sa.JSON(none_as_null=True), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("source", "source_context")
