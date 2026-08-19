"""Version source snapshots by URL and content hash.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-28
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("source")}
    if "content_hash" not in columns:
        op.add_column(
            "source",
            sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        )

    source = sa.table(
        "source",
        sa.column("id", sa.String(36)),
        sa.column("content", sa.Text()),
        sa.column("content_hash", sa.String(64)),
    )
    rows = bind.execute(sa.select(source.c.id, source.c.content)).all()
    for row in rows:
        content_hash = hashlib.sha256((row.content or "").encode("utf-8")).hexdigest()
        bind.execute(source.update().where(source.c.id == row.id).values(content_hash=content_hash))

    unique_constraints = {
        constraint.get("name") for constraint in sa.inspect(bind).get_unique_constraints("source")
    }
    with op.batch_alter_table("source") as batch:
        if "uq_source_run_url" in unique_constraints:
            batch.drop_constraint("uq_source_run_url", type_="unique")
        if "uq_source_run_snapshot" not in unique_constraints:
            batch.create_unique_constraint(
                "uq_source_run_snapshot", ["run_id", "url", "content_hash"]
            )


def downgrade() -> None:
    bind = op.get_bind()
    duplicate_urls = bind.execute(
        sa.text("SELECT run_id, url FROM source GROUP BY run_id, url HAVING COUNT(*) > 1")
    ).first()
    if duplicate_urls is not None:
        raise RuntimeError("cannot downgrade source snapshots while URL versions exist")
    with op.batch_alter_table("source") as batch:
        batch.drop_constraint("uq_source_run_snapshot", type_="unique")
        batch.create_unique_constraint("uq_source_run_url", ["run_id", "url"])
        batch.drop_column("content_hash")
