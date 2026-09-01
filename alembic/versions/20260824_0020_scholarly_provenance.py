"""Persist scholarly provenance: source metadata and rendered citations.

``source.scholarly`` holds the DOI / authors / affiliations / venue / retraction
metadata returned by the scholarly backends (OpenAlex, arXiv).  It is a JSON column
rather than a dozen typed columns because the payload arrives, is stored, and is
consumed as one unit — nothing queries it field-by-field, and splitting it would
make every new provenance field cost a migration.

``finding.source_reference`` stores the citation string rendered at verification
time, when the finding and its source are both in hand.  Denormalising it here is
what lets a replayed run, or a run executed in a separate worker process, render
exactly the same reference list as the original.

Both columns are additive and nullable/defaulted, so existing rows stay valid and
reports built from them fall back to the previous bare-URL form.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    finding_columns = {column["name"] for column in inspector.get_columns("finding")}
    if "source_reference" not in finding_columns:
        op.add_column(
            "finding",
            sa.Column("source_reference", sa.Text(), nullable=False, server_default=""),
        )

    source_columns = {column["name"] for column in inspector.get_columns("source")}
    if "scholarly" not in source_columns:
        op.add_column("source", sa.Column("scholarly", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("source", "scholarly")
    op.drop_column("finding", "source_reference")
