"""角色广场 catalog：model_profile / agent_card / search_key

模型档案（可复用的 LLM 端点配置）、角色卡片（数据驱动角色定义，按角色绑模型）、
搜索 key 池（主备故障转移）。三表独立于单次 run，属全局配置。

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_profile",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("api_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=100), nullable=False, server_default="gpt-4o-mini"),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.3"),
        sa.Column("is_default", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_model_profile_name"),
    )
    op.create_table(
        "agent_card",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("behavior", sa.String(length=20), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("icon", sa.String(length=16), nullable=False, server_default="🧩"),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("model_profile_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["model_profile_id"], ["model_profile.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_agent_card_name"),
    )
    op.create_index(op.f("ix_agent_card_model_profile_id"), "agent_card", ["model_profile_id"])
    op.create_table(
        "search_key",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("search_key")
    op.drop_index(op.f("ix_agent_card_model_profile_id"), table_name="agent_card")
    op.drop_table("agent_card")
    op.drop_table("model_profile")
