"""Academic Q&A conversations and messages for the research workbench."""

import sqlalchemy as sa

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "qa_conversation" not in tables:
        op.create_table(
            "qa_conversation",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_id", sa.String(64), nullable=False),
            sa.Column("title", sa.String(200), nullable=False, server_default=""),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_qa_conversation_owner_id", "qa_conversation", ["owner_id"])
    if "qa_message" not in tables:
        op.create_table(
            "qa_message",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "conversation_id",
                sa.String(36),
                sa.ForeignKey("qa_conversation.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("query", sa.Text(), nullable=False),
            sa.Column("answer", sa.Text(), nullable=False, server_default=""),
            sa.Column("citations", sa.JSON(), nullable=False),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.Column("thoughts", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="done"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("conversation_id", "position", name="uq_qa_message_position"),
        )
        op.create_index("ix_qa_message_conversation_id", "qa_message", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_qa_message_conversation_id", table_name="qa_message")
    op.drop_table("qa_message")
    op.drop_index("ix_qa_conversation_owner_id", table_name="qa_conversation")
    op.drop_table("qa_conversation")
