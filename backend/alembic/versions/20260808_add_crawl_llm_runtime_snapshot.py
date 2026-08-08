"""add crawl LLM runtime snapshot

Revision ID: 20260808_crawl_llm_snapshot
Revises: 20260807_match_task_decoupling
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260808_crawl_llm_snapshot"
down_revision: str | Sequence[str] | None = "20260807_match_task_decoupling"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "crawl_job_runs",
        sa.Column("llm_runtime_snapshot", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("crawl_job_runs", "llm_runtime_snapshot")
