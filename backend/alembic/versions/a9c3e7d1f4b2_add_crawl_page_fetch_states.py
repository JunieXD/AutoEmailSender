from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a9c3e7d1f4b2"
down_revision = "c4b8e2a9d6f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crawl_page_fetch_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("normalized_url", sa.String(length=1000), nullable=False),
        sa.Column("original_url", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("last_fetch_method", sa.String(length=64), nullable=True),
        sa.Column("terminal_reason", sa.String(length=128), nullable=True),
        sa.Column("transient_failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("last_page_id", sa.Integer(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["crawl_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_page_id"], ["crawl_pages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "normalized_url", name="uq_crawl_page_fetch_states_job_url"),
    )
    op.create_index("ix_crawl_page_fetch_states_job_id", "crawl_page_fetch_states", ["job_id"])
    op.create_index("ix_crawl_page_fetch_states_status", "crawl_page_fetch_states", ["status"])


def downgrade() -> None:
    op.drop_index("ix_crawl_page_fetch_states_status", table_name="crawl_page_fetch_states")
    op.drop_index("ix_crawl_page_fetch_states_job_id", table_name="crawl_page_fetch_states")
    op.drop_table("crawl_page_fetch_states")
