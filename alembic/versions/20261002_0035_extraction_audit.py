"""Persist extraction candidates and targeted repair history.

Revision ID: 0035
Revises: 0034
"""

import sqlalchemy as sa

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("research_result")
    }
    if "extraction_audit" not in columns:
        op.add_column("research_result", sa.Column("extraction_audit", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("research_result") as batch:
        batch.drop_column("extraction_audit")
