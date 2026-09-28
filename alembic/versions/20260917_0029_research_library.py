"""Persistent projects, corpora, source chunks and run bindings."""

import sqlalchemy as sa

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    if "research_project" not in tables:
        op.create_table(
            "research_project",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_id", sa.String(64), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
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
            sa.UniqueConstraint("owner_id", "name", name="uq_research_project_owner_name"),
        )
        op.create_index("ix_research_project_owner_id", "research_project", ["owner_id"])
    if "corpus" not in tables:
        op.create_table(
            "corpus",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "project_id",
                sa.String(36),
                sa.ForeignKey("research_project.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
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
            sa.UniqueConstraint("project_id", "name", name="uq_corpus_project_name"),
        )
        op.create_index("ix_corpus_project_id", "corpus", ["project_id"])
    if "library_source" not in tables:
        op.create_table(
            "library_source",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "project_id",
                sa.String(36),
                sa.ForeignKey("research_project.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "corpus_id",
                sa.String(36),
                sa.ForeignKey("corpus.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("title", sa.String(300), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="included"),
            sa.Column("origin_url", sa.Text(), nullable=False, server_default=""),
            sa.Column("mime_type", sa.String(100), nullable=False, server_default="text/plain"),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("char_count", sa.Integer(), nullable=False),
            sa.Column("source_metadata", sa.JSON(), nullable=False),
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
            sa.UniqueConstraint("corpus_id", "content_hash", name="uq_library_source_snapshot"),
        )
        op.create_index("ix_library_source_project_id", "library_source", ["project_id"])
        op.create_index("ix_library_source_corpus_id", "library_source", ["corpus_id"])
        op.create_index(
            "ix_library_source_project_status", "library_source", ["project_id", "status"]
        )
    if "source_chunk" not in tables:
        op.create_table(
            "source_chunk",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "source_id",
                sa.String(36),
                sa.ForeignKey("library_source.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("locator", sa.String(300), nullable=False),
            sa.Column("start_char", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("end_char", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("page_start", sa.Integer(), nullable=True),
            sa.Column("page_end", sa.Integer(), nullable=True),
            sa.Column("section", sa.String(300), nullable=False, server_default=""),
            sa.UniqueConstraint("source_id", "ordinal", name="uq_source_chunk_ordinal"),
        )
        op.create_index("ix_source_chunk_source_id", "source_chunk", ["source_id"])
    run_columns = {column["name"] for column in inspector.get_columns("research_run")}
    if "project_id" not in run_columns:
        with op.batch_alter_table("research_run") as batch:
            batch.add_column(sa.Column("project_id", sa.String(36), nullable=True))
            batch.create_foreign_key(
                "fk_research_run_project_id",
                "research_project",
                ["project_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_index("ix_research_run_project_id", ["project_id"])
    source_columns = {column["name"] for column in inspector.get_columns("source")}
    if "locator" not in source_columns:
        with op.batch_alter_table("source") as batch:
            batch.add_column(
                sa.Column("locator", sa.String(300), nullable=False, server_default="")
            )


def downgrade() -> None:
    with op.batch_alter_table("source") as batch:
        batch.drop_column("locator")
    with op.batch_alter_table("research_run") as batch:
        batch.drop_index("ix_research_run_project_id")
        batch.drop_constraint("fk_research_run_project_id", type_="foreignkey")
        batch.drop_column("project_id")
    op.drop_index("ix_source_chunk_source_id", table_name="source_chunk")
    op.drop_table("source_chunk")
    op.drop_index("ix_library_source_project_status", table_name="library_source")
    op.drop_index("ix_library_source_corpus_id", table_name="library_source")
    op.drop_index("ix_library_source_project_id", table_name="library_source")
    op.drop_table("library_source")
    op.drop_index("ix_corpus_project_id", table_name="corpus")
    op.drop_table("corpus")
    op.drop_index("ix_research_project_owner_id", table_name="research_project")
    op.drop_table("research_project")
