"""finding.entity：对照表的行标识

对照表的行是"被比较的对象"（方法名 / 光学方案名 / 数据集名），不是"论文"——
一篇论文常同时报告自己与多个 baseline 的数字，按论文聚合会把它们压进一格。

新列 NOT NULL DEFAULT ''：历史行拿到空串＝"未标注对象"，透视器据此跳过它们，
既有 run 的报告产物不变。

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_COLUMN = sa.Column("entity", sa.Text(), nullable=False, server_default="")


def _has_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(column["name"] == "entity" for column in inspector.get_columns("finding"))


def upgrade() -> None:
    # 幂等：与本项目既有迁移一致，容忍手工建过表的历史部署。
    if not _has_column():
        op.add_column("finding", _COLUMN)


def downgrade() -> None:
    if _has_column():
        op.drop_column("finding", "entity")
