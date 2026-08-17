"""track skipped candidates for crawl enrichment operations

Revision ID: 20260817_crawl_enrichment_skip
Revises: 20260817_crawl_enrichment_op
Create Date: 2026-08-17 00:00:01.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260817_crawl_enrichment_skip"
down_revision: str | Sequence[str] | None = "20260817_crawl_enrichment_op"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _crawl_job_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {str(column["name"]) for column in inspector.get_columns("crawl_jobs")}


def upgrade() -> None:
    if "active_candidate_enrichment_skipped_count" in _crawl_job_columns():
        return

    with op.batch_alter_table("crawl_jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "active_candidate_enrichment_skipped_count",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )


def downgrade() -> None:
    if "active_candidate_enrichment_skipped_count" not in _crawl_job_columns():
        return

    with op.batch_alter_table("crawl_jobs", schema=None) as batch_op:
        batch_op.drop_column("active_candidate_enrichment_skipped_count")
