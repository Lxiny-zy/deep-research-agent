"""Retain confirmed PDF author metadata without asserting publication status.

Revision ID: 0036
Revises: 0035
"""

import sqlalchemy as sa

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("source")}
    if "document_authors" not in columns:
        op.add_column("source", sa.Column("document_authors", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("source") as batch:
        batch.drop_column("document_authors")
