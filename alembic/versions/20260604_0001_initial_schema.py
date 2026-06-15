"""initial schema

把一次研究运行的全过程建表持久化：
research_run ─┬─ sub_question
              ├─ research_result ─ finding
              ├─ source
              ├─ report (1:1)
              └─ event（按 (run_id, seq) 单调，支持回放）

字段/约束严格对应 deep_research.persistence.orm；类型用通用类型，
跨 SQLite（测试）与 PostgreSQL（生产）一致。

Revision ID: 0001
Revises:
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_run",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("interpretation", sa.Text(), nullable=False),
        sa.Column("elapsed", sa.Float(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "sub_question",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("depends_on", sa.JSON(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sub_question_run_id"), "sub_question", ["run_id"])

    op.create_table(
        "research_result",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sub_question", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_research_result_run_id"), "research_result", ["run_id"])

    op.create_table(
        "finding",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("result_id", sa.String(length=36), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["result_id"], ["research_result.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_finding_result_id"), "finding", ["result_id"])

    op.create_table(
        "source",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "url", name="uq_source_run_url"),
    )
    op.create_index(op.f("ix_source_run_id"), "source", ["run_id"])

    op.create_table(
        "report",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_report_run_id"), "report", ["run_id"], unique=True)

    op.create_table(
        "event",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("elapsed", sa.Float(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["research_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "seq", name="uq_event_run_seq"),
    )
    op.create_index(op.f("ix_event_run_id"), "event", ["run_id"])


def downgrade() -> None:
    # 反序删除：先子后父，避开外键依赖
    op.drop_index(op.f("ix_event_run_id"), table_name="event")
    op.drop_table("event")
    op.drop_index(op.f("ix_report_run_id"), table_name="report")
    op.drop_table("report")
    op.drop_index(op.f("ix_source_run_id"), table_name="source")
    op.drop_table("source")
    op.drop_index(op.f("ix_finding_result_id"), table_name="finding")
    op.drop_table("finding")
    op.drop_index(op.f("ix_research_result_run_id"), table_name="research_result")
    op.drop_table("research_result")
    op.drop_index(op.f("ix_sub_question_run_id"), table_name="sub_question")
    op.drop_table("sub_question")
    op.drop_table("research_run")
