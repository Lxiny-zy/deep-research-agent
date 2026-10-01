"""Share provisional Q&A stream events across API processes.

Revision ID: 0034
Revises: 0033
"""

import sqlalchemy as sa

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("qa_stream_event"):
        return
    op.create_table(
        "qa_stream_event",
        sa.Column("message_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["qa_message.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("message_id", "sequence"),
    )


def downgrade() -> None:
    op.drop_table("qa_stream_event")
