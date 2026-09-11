"""Shared provider credential leases and per-identity HTTP admission."""

import sqlalchemy as sa

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "provider_state" not in tables:
        op.create_table(
            "provider_state",
            sa.Column("fingerprint", sa.String(64), primary_key=True),
            sa.Column("retry_at", sa.Float(), nullable=False),
            sa.Column("window_start", sa.Float(), nullable=False),
            sa.Column("window_count", sa.Integer(), nullable=False),
            sa.Column("requests", sa.Integer(), nullable=False),
            sa.Column("limited", sa.Integer(), nullable=False),
        )
    if "provider_lease" not in tables:
        op.create_table(
            "provider_lease",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("fingerprint", sa.String(64), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
        )
        op.create_index("ix_provider_lease_fingerprint", "provider_lease", ["fingerprint"])
    if "request_window" not in tables:
        op.create_table(
            "request_window",
            sa.Column("identity", sa.String(64), primary_key=True),
            sa.Column("starts_at", sa.Float(), nullable=False),
            sa.Column("count", sa.Integer(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("request_window")
    op.drop_index("ix_provider_lease_fingerprint", table_name="provider_lease")
    op.drop_table("provider_lease")
    op.drop_table("provider_state")
