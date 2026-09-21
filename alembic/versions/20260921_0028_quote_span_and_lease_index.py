"""Citation quote offsets, plus an index for the lease predicate.

Two unrelated-looking changes ship together because both are pure schema
additions with no data backfill:

* ``finding.quote_start`` / ``finding.quote_end`` persist the character span the
  evidence verifier already computes.  Paired with ``source_content_hash`` they
  turn a citation anchor from "which source" into "which passage", so any single
  citation can be independently relocated and re-checked against the same
  snapshot.  NULL means a historical row, which keeps the previous behaviour of
  having only the rendered ``evidence_context`` window.

* ``ix_workflow_run_lease`` covers ``(lease_expires_at, lease_owner)``.  Every
  worker claim query and every fenced write filters on those columns, and until
  now only ``research_run_id`` was indexed.
"""

import sqlalchemy as sa

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    finding_columns = {column["name"] for column in inspector.get_columns("finding")}
    missing = [name for name in ("quote_start", "quote_end") if name not in finding_columns]
    if missing:
        with op.batch_alter_table("finding") as batch:
            for name in missing:
                # Nullable with no server default: absence is meaningful here and
                # must stay distinguishable from a real offset of 0.
                batch.add_column(sa.Column(name, sa.Integer(), nullable=True))

    indexes = {index["name"] for index in inspector.get_indexes("workflow_run")}
    if "ix_workflow_run_lease" not in indexes:
        op.create_index(
            "ix_workflow_run_lease",
            "workflow_run",
            ["lease_expires_at", "lease_owner"],
        )


def downgrade() -> None:
    op.drop_index("ix_workflow_run_lease", table_name="workflow_run")
    with op.batch_alter_table("finding") as batch:
        batch.drop_column("quote_end")
        batch.drop_column("quote_start")
