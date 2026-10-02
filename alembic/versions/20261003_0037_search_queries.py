"""Persist search expressions separately from complete research questions.

Revision ID: 0037
Revises: 0036
"""

import sqlalchemy as sa

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("sub_question")}
    if "search_queries" not in columns:
        op.add_column(
            "sub_question",
            sa.Column("search_queries", sa.JSON(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    with op.batch_alter_table("sub_question") as batch:
        batch.drop_column("search_queries")
