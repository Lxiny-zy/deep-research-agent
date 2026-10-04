"""Durable document rendering queue and fenced ownership."""

import sqlalchemy as sa

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "render_job" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "render_job",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False, unique=True),
        sa.Column("pool", sa.String(64), nullable=False),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("research_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("queued_at", sa.Float(), nullable=False),
        sa.Column("available_at", sa.Float(), nullable=False),
        sa.Column("lease_owner", sa.String(96), nullable=True),
        sa.Column("lease_until", sa.Float(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("stalls", sa.Integer(), nullable=False),
        sa.Column("interrupted", sa.Boolean(), nullable=False),
        sa.Column("progress_token", sa.String(64), nullable=False),
        sa.Column("request_tokens", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','running','done','error','cancelled')",
            name="ck_render_job_status",
        ),
        sa.CheckConstraint("attempts >= 0 AND stalls >= 0", name="ck_render_job_attempts"),
        sa.CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL) "
            "OR (status <> 'running' AND lease_owner IS NULL AND lease_until IS NULL)",
            name="ck_render_job_lease",
        ),
    )
    op.create_index("ix_render_job_run_id", "render_job", ["run_id"])
    op.create_index(
        "ix_render_job_queue", "render_job", ["pool", "status", "available_at", "queued_at"]
    )


def downgrade() -> None:
    op.drop_table("render_job")
