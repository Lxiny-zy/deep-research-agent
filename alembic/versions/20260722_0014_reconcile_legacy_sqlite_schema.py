"""Reconcile databases previously stamped from legacy create_all schemas.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-22

Older local SQLite startup code called ``create_all`` and later stamped those
databases at the Alembic head. ``create_all`` creates missing tables but never
adds columns or constraints to existing tables, so some databases reported a
current revision while still having an older physical schema. This migration
is intentionally idempotent and also remains portable to PostgreSQL.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _revision_metadata() -> sa.MetaData:
    """Return the schema as it existed at revision 0014.

    Alembic revisions must not import live ORM metadata: a future ORM table or
    column would otherwise be created while this historical revision runs,
    before the migration that owns it.  Keep this snapshot aligned with the
    final DDL produced by revisions 0001 through 0013.
    """
    metadata = sa.MetaData()

    sa.Table(
        "research_run",
        metadata,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("interpretation", sa.Text(), nullable=False),
        sa.Column("elapsed", sa.Float(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    sub_question = sa.Table(
        "sub_question",
        metadata,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("depends_on", sa.JSON(), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["research_run.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "idx", name="uq_subquestion_run_idx"),
    )
    sa.Index("ix_sub_question_run_id", sub_question.c.run_id)

    research_result = sa.Table(
        "research_result",
        metadata,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("sub_question", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["research_run.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Index("ix_research_result_run_id", research_result.c.run_id)

    finding = sa.Table(
        "finding",
        metadata,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("result_id", sa.String(36), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_quote", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "verification_status",
            sa.String(16),
            nullable=False,
            server_default="unverified",
        ),
        sa.Column(
            "verification_method",
            sa.String(32),
            nullable=False,
            server_default="none",
        ),
        sa.Column(
            "source_content_hash",
            sa.String(64),
            nullable=False,
            server_default="",
        ),
        sa.Column("verification_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "semantic_status",
            sa.String(16),
            nullable=False,
            server_default="not_checked",
        ),
        sa.Column(
            "semantic_confidence",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("semantic_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("claim_id", sa.String(32), nullable=False, server_default=""),
        sa.Column(
            "consistency_status",
            sa.String(16),
            nullable=False,
            server_default="not_checked",
        ),
        sa.Column(
            "contradicts_claim_ids",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("contradiction_reason", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(
            ["result_id"], ["research_result.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Index("ix_finding_result_id", finding.c.result_id)

    source = sa.Table(
        "source",
        metadata,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["research_run.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "url", name="uq_source_run_url"),
    )
    sa.Index("ix_source_run_id", source.c.run_id)

    report = sa.Table(
        "report",
        metadata,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["research_run.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Index("ix_report_run_id", report.c.run_id, unique=True)

    event = sa.Table(
        "event",
        metadata,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("elapsed", sa.Float(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"], ["research_run.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "seq", name="uq_event_run_seq"),
    )
    sa.Index("ix_event_run_id", event.c.run_id)

    run_tag = sa.Table(
        "run_tag",
        metadata,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("tag", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["research_run.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "tag", name="uq_run_tag"),
    )
    sa.Index("ix_run_tag_run_id", run_tag.c.run_id)

    sa.Table(
        "model_profile",
        metadata,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("api_key", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "model",
            sa.String(100),
            nullable=False,
            server_default="gpt-4o-mini",
        ),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.3"),
        sa.Column("is_default", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "parameter_mode",
            sa.String(16),
            nullable=False,
            server_default="temperature",
        ),
        sa.Column(
            "reasoning_effort",
            sa.String(16),
            nullable=False,
            server_default="medium",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_model_profile_name"),
    )

    agent_card = sa.Table(
        "agent_card",
        metadata,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("behavior", sa.String(20), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "icon",
            sa.String(16),
            nullable=False,
            server_default="\U0001f9e9",
        ),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("model_profile_id", sa.String(36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["model_profile_id"], ["model_profile.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_agent_card_name"),
    )
    sa.Index("ix_agent_card_model_profile_id", agent_card.c.model_profile_id)

    sa.Table(
        "search_key",
        metadata,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("label", sa.String(64), nullable=False, server_default=""),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    sa.Table(
        "workflow_def",
        metadata,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("steps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
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
        sa.Column("nodes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("edges", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("viewport", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_workflow_def_name"),
    )

    workflow_run = sa.Table(
        "workflow_run",
        metadata,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("research_run_id", sa.String(36), nullable=False),
        sa.Column("workflow_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("definition", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("checkpoint", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["research_run_id"], ["research_run.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("research_run_id"),
    )
    sa.Index("ix_workflow_run_research_run_id", workflow_run.c.research_run_id)

    step_run = sa.Table(
        "step_run",
        metadata,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workflow_run_id", sa.String(36), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(100), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_run.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Index("ix_step_run_workflow_run_id", step_run.c.workflow_run_id)

    return metadata


def _column_repairs() -> dict[str, list[sa.Column[object]]]:
    return {
        "workflow_def": [
            sa.Column("nodes", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("edges", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("viewport", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        ],
        "workflow_run": [
            sa.Column("definition", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("checkpoint", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("lease_owner", sa.String(64), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        ],
        "model_profile": [
            sa.Column(
                "parameter_mode",
                sa.String(16),
                nullable=False,
                server_default="temperature",
            ),
            sa.Column(
                "reasoning_effort",
                sa.String(16),
                nullable=False,
                server_default="medium",
            ),
        ],
        "finding": [
            sa.Column("evidence_quote", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "verification_status",
                sa.String(16),
                nullable=False,
                server_default="unverified",
            ),
            sa.Column(
                "verification_method",
                sa.String(32),
                nullable=False,
                server_default="none",
            ),
            sa.Column(
                "source_content_hash",
                sa.String(64),
                nullable=False,
                server_default="",
            ),
            sa.Column("verification_reason", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "semantic_status",
                sa.String(16),
                nullable=False,
                server_default="not_checked",
            ),
            sa.Column(
                "semantic_confidence", sa.Float(), nullable=False, server_default="0"
            ),
            sa.Column("semantic_reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("claim_id", sa.String(32), nullable=False, server_default=""),
            sa.Column(
                "consistency_status",
                sa.String(16),
                nullable=False,
                server_default="not_checked",
            ),
            sa.Column(
                "contradicts_claim_ids",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            ),
            sa.Column("contradiction_reason", sa.Text(), nullable=False, server_default=""),
        ],
    }


def _add_missing_columns(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    for table_name, columns in _column_repairs().items():
        if not inspector.has_table(table_name):
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column in columns:
            if column.name not in existing:
                op.add_column(table_name, column)
                existing.add(column.name)


def _tighten_timestamps(bind: sa.Connection) -> None:
    timestamp_columns = (
        ("model_profile", "created_at"),
        ("agent_card", "created_at"),
        ("search_key", "created_at"),
        ("workflow_def", "created_at"),
        ("workflow_def", "updated_at"),
    )
    for table_name, column_name in timestamp_columns:
        inspector = sa.inspect(bind)
        if not inspector.has_table(table_name):
            continue
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        column = columns.get(column_name)
        if column is None or not column.get("nullable", True):
            continue
        table = sa.table(table_name, sa.column(column_name, sa.DateTime(timezone=True)))
        op.execute(
            table.update()
            .where(table.c[column_name].is_(None))
            .values({column_name: sa.func.now()})
        )
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                column_name,
                existing_type=column["type"],
                existing_server_default=column.get("default") or sa.func.now(),
                nullable=False,
            )


def _renumber_duplicate_sub_questions(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.text("SELECT id, run_id, idx FROM sub_question ORDER BY run_id, idx, id")
    ).mappings()
    grouped: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["run_id"])].append((str(row["id"]), int(row["idx"])))

    for run_id, entries in grouped.items():
        indexes = [idx for _, idx in entries]
        if len(indexes) == len(set(indexes)):
            continue
        # Move the group out of the target range first so reassignment cannot
        # collide if a partially repaired database already has another index.
        offset = max(indexes, default=0) + len(entries) + 1
        bind.execute(
            sa.text("UPDATE sub_question SET idx = idx + :offset WHERE run_id = :run_id"),
            {"offset": offset, "run_id": run_id},
        )
        for new_idx, (row_id, _) in enumerate(entries):
            bind.execute(
                sa.text("UPDATE sub_question SET idx = :idx WHERE id = :id"),
                {"idx": new_idx, "id": row_id},
            )


def _ensure_sub_question_unique(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    if not inspector.has_table("sub_question"):
        return
    target = {"run_id", "idx"}
    constraints = inspector.get_unique_constraints("sub_question")
    indexes = inspector.get_indexes("sub_question")
    # A plain composite index does not enforce the invariant.  SQLite's
    # inspector reports both indexes and unique constraints here, so only
    # count an index when its dialect explicitly marks it unique.
    if any(set(item.get("column_names") or []) == target for item in constraints) or any(
        set(item.get("column_names") or []) == target and bool(item.get("unique"))
        for item in indexes
    ):
        return
    _renumber_duplicate_sub_questions(bind)
    with op.batch_alter_table("sub_question") as batch_op:
        batch_op.create_unique_constraint("uq_subquestion_run_idx", ["run_id", "idx"])


def upgrade() -> None:
    bind = op.get_bind()
    # Creates tables introduced after a legacy create_all snapshot. Existing
    # tables are left untouched and reconciled below.
    _revision_metadata().create_all(bind=bind)
    _add_missing_columns(bind)
    _tighten_timestamps(bind)
    _ensure_sub_question_unique(bind)


def downgrade() -> None:
    # Revision 0013 already expects every repaired table, column and constraint.
    # Downgrading the reconciliation marker must therefore keep the schema.
    pass
