"""Bind paper-reading Q&A conversations to their research run."""

import sqlalchemy as sa

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("qa_conversation")
    }
    if "run_id" not in columns:
        op.add_column("qa_conversation", sa.Column("run_id", sa.String(64), nullable=True))
        op.create_index("ix_qa_conversation_run_id", "qa_conversation", ["run_id"])


def downgrade() -> None:
    # batch 模式：SQLite 不支持直接删列，需要重建表
    with op.batch_alter_table("qa_conversation") as batch:
        batch.drop_index("ix_qa_conversation_run_id")
        batch.drop_column("run_id")
