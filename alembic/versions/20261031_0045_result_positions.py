"""Read research results and findings back in the order they were written."""

import sqlalchemy as sa

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

_TABLES = ("research_result", "finding")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in _TABLES:
        if "position" not in {item["name"] for item in inspector.get_columns(table)}:
            # Historical rows stay NULL and keep the order the database returns.
            op.add_column(table, sa.Column("position", sa.Integer(), nullable=True))


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "position")
