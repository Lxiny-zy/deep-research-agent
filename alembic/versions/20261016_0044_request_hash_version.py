"""Version idempotency hashes without rewriting historical request bodies."""

import sqlalchemy as sa

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("research_run")}
    if "request_hash_version" not in columns:
        op.add_column("research_run", sa.Column(
            "request_hash_version", sa.Integer(), nullable=False, server_default="1",
        ))


def downgrade() -> None:
    # SQLite >= 3.35 supports DROP COLUMN without rebuilding the parent table.
    # An older SQLite must reject the downgrade rather than cascading children.
    op.drop_column("research_run", "request_hash_version")
