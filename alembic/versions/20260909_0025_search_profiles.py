"""Search profiles, role search bindings and explicit prompt modes.

Existing prompts retain replacement semantics; no secret is copied or rewritten.
"""

import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "search_profile" not in inspector.get_table_names():
        op.create_table(
            "search_profile",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(100), nullable=False, unique=True),
            sa.Column("provider", sa.String(16), nullable=False),
            sa.Column("endpoint", sa.String(500), nullable=False, server_default=""),
            sa.Column("model", sa.String(100), nullable=False, server_default=""),
            sa.Column("key_ids", sa.JSON(), nullable=False),
            sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        )
    columns = {col["name"] for col in inspector.get_columns("agent_card")}
    if "prompt_mode" not in columns:
        op.add_column(
            "agent_card",
            sa.Column("prompt_mode", sa.String(16), nullable=False, server_default="replace"),
        )
    if "search_profile_ids" not in columns:
        op.add_column("agent_card", sa.Column("search_profile_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_card", "search_profile_ids")
    op.drop_column("agent_card", "prompt_mode")
    op.drop_table("search_profile")
