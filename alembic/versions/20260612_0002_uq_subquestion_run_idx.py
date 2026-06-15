"""sub_question (run_id, idx) 唯一约束

add_sub_questions 是 count-then-insert，无约束时并发复用仓储会静默产生重复 idx
破坏排序语义；加唯一约束让这种情况显式失败而非数据损坏。

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-12
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sub_question") as batch:  # batch 模式兼容 SQLite
        batch.create_unique_constraint("uq_subquestion_run_idx", ["run_id", "idx"])


def downgrade() -> None:
    with op.batch_alter_table("sub_question") as batch:
        batch.drop_constraint("uq_subquestion_run_idx", type_="unique")
