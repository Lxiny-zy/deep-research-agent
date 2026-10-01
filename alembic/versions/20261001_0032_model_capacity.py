"""Per-model context and output capacities; NULL uses provider defaults."""

import sqlalchemy as sa

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("model_profile")}
    for name in ("context_window_tokens", "max_output_tokens"):
        if name not in existing:
            op.add_column("model_profile", sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("model_profile") as batch:
        batch.drop_column("max_output_tokens")
        batch.drop_column("context_window_tokens")
