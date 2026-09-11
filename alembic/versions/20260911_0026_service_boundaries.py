"""Workspace ownership, replayable streams and shared service coordination."""

import sqlalchemy as sa

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "owner_id" not in {col["name"] for col in inspector.get_columns("research_run")}:
        op.add_column("research_run", sa.Column("owner_id", sa.String(64), nullable=True))
        op.create_index("ix_research_run_owner_id", "research_run", ["owner_id"])
    columns = {col["name"] for col in inspector.get_columns("event")}
    if "tokens" not in columns:
        op.add_column(
            "event", sa.Column("tokens", sa.Integer(), nullable=False, server_default="0")
        )
    if "tokens_estimated" not in columns:
        op.add_column(
            "event", sa.Column("tokens_estimated", sa.Boolean(), nullable=False, server_default="0")
        )
    tables = set(inspector.get_table_names())
    if "coordination" not in tables:
        op.create_table("coordination", sa.Column("name", sa.String(64), primary_key=True))
    if "runtime_config_revision" not in tables:
        op.create_table(
            "runtime_config_revision",
            sa.Column("version", sa.Integer(), primary_key=True),
            sa.Column("values", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    if "worker_heartbeat" not in tables:
        op.create_table(
            "worker_heartbeat",
            sa.Column("name", sa.String(64), primary_key=True),
            sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("active", sa.Integer(), nullable=False),
        )
    if "artifact_cleanup" not in tables:
        op.create_table(
            "artifact_cleanup",
            sa.Column("run_id", sa.String(36), primary_key=True),
            sa.Column("slug", sa.String(200), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    connection = op.get_bind()
    defaults = list(
        connection.execute(
            sa.text("SELECT id FROM model_profile WHERE is_default = 1 ORDER BY created_at, id")
        ).scalars()
    )
    for profile_id in defaults[1:]:
        connection.execute(
            sa.text("UPDATE model_profile SET is_default = 0 WHERE id = :id"),
            {"id": profile_id},
        )
    if "uq_model_profile_default" not in {
        index["name"] for index in inspector.get_indexes("model_profile")
    }:
        op.create_index(
            "uq_model_profile_default",
            "model_profile",
            ["is_default"],
            unique=True,
            sqlite_where=sa.text("is_default = 1"),
            postgresql_where=sa.text("is_default = 1"),
        )


def downgrade() -> None:
    op.drop_index("uq_model_profile_default", table_name="model_profile")
    for table in (
        "artifact_cleanup",
        "worker_heartbeat",
        "runtime_config_revision",
        "coordination",
    ):
        op.drop_table(table)
    op.drop_column("event", "tokens_estimated")
    op.drop_column("event", "tokens")
    op.drop_index("ix_research_run_owner_id", table_name="research_run")
    op.drop_column("research_run", "owner_id")
