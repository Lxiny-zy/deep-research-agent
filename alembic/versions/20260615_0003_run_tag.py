"""run_tag 表：给研究运行打标签 / 分类

每条 (run_id, tag) 唯一；随 run 级联删除。支持历史页按标签筛选。

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_tag",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "tag", name="uq_run_tag"),
    )
    op.create_index(op.f("ix_run_tag_run_id"), "run_tag", ["run_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_run_tag_run_id"), table_name="run_tag")
    op.drop_table("run_tag")
