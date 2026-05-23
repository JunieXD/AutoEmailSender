from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crawl_page_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("page_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("chunk_id", sa.String(length=255), nullable=False),
        sa.Column("parent_chunk_id", sa.String(length=255), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=64), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("text_start_offset", sa.Integer(), nullable=True),
        sa.Column("text_end_offset", sa.Integer(), nullable=True),
        sa.Column("overlap_prefix", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("overlap_suffix", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("split_depth", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("split_reason", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["crawl_jobs.id"], name=op.f("fk_crawl_page_chunks_job_id_crawl_jobs"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["crawl_pages.id"], name=op.f("fk_crawl_page_chunks_page_id_crawl_pages"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_page_chunks")),
        sa.UniqueConstraint("job_id", "chunk_id", name=op.f("uq_crawl_page_chunks_job_chunk_id")),
    )
    op.create_index(op.f("ix_crawl_page_chunks_job_id"), "crawl_page_chunks", ["job_id"], unique=False)
    op.create_index(op.f("ix_crawl_page_chunks_page_id"), "crawl_page_chunks", ["page_id"], unique=False)
    op.create_index(op.f("ix_crawl_page_chunks_parent_chunk_id"), "crawl_page_chunks", ["parent_chunk_id"], unique=False)
    op.create_index(op.f("ix_crawl_page_chunks_chunk_hash"), "crawl_page_chunks", ["chunk_hash"], unique=False)
    op.create_index(op.f("ix_crawl_page_chunks_status"), "crawl_page_chunks", ["status"], unique=False)

    with op.batch_alter_table("crawl_candidates") as batch_op:
        batch_op.add_column(sa.Column("source_chunk_id", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("source_kind", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("boundary_risk", sa.Boolean(), server_default=sa.text("0"), nullable=False))
        batch_op.add_column(sa.Column("identity_key", sa.String(length=1000), nullable=True))
        batch_op.add_column(sa.Column("merge_history", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("field_sources", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("conflicts", sa.JSON(), nullable=True))
        batch_op.create_index(batch_op.f("ix_crawl_candidates_identity_key"), ["identity_key"], unique=False)
        batch_op.create_index(batch_op.f("ix_crawl_candidates_source_chunk_id"), ["source_chunk_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("crawl_candidates") as batch_op:
        batch_op.drop_index(batch_op.f("ix_crawl_candidates_source_chunk_id"))
        batch_op.drop_index(batch_op.f("ix_crawl_candidates_identity_key"))
        batch_op.drop_column("conflicts")
        batch_op.drop_column("field_sources")
        batch_op.drop_column("merge_history")
        batch_op.drop_column("identity_key")
        batch_op.drop_column("boundary_risk")
        batch_op.drop_column("source_kind")
        batch_op.drop_column("source_chunk_id")

    op.drop_index(op.f("ix_crawl_page_chunks_status"), table_name="crawl_page_chunks")
    op.drop_index(op.f("ix_crawl_page_chunks_chunk_hash"), table_name="crawl_page_chunks")
    op.drop_index(op.f("ix_crawl_page_chunks_parent_chunk_id"), table_name="crawl_page_chunks")
    op.drop_index(op.f("ix_crawl_page_chunks_page_id"), table_name="crawl_page_chunks")
    op.drop_index(op.f("ix_crawl_page_chunks_job_id"), table_name="crawl_page_chunks")
    op.drop_table("crawl_page_chunks")
