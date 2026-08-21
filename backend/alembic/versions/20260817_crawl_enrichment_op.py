"""track active crawl candidate enrichment operations

Revision ID: 20260817_crawl_enrichment_op
Revises: 20260812_merge_beta_master
Create Date: 2026-08-17 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260817_crawl_enrichment_op"
down_revision: str | Sequence[str] | None = "20260812_merge_beta_master"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _crawl_job_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {str(column["name"]) for column in inspector.get_columns("crawl_jobs")}


def upgrade() -> None:
    if "active_candidate_enrichment_operation_id" in _crawl_job_columns():
        return

    with op.batch_alter_table("crawl_jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "active_candidate_enrichment_operation_id",
                sa.String(length=36),
                nullable=True,
            )
        )


def downgrade() -> None:
    if "active_candidate_enrichment_operation_id" not in _crawl_job_columns():
        return

    with op.batch_alter_table("crawl_jobs", schema=None) as batch_op:
        batch_op.drop_column("active_candidate_enrichment_operation_id")
