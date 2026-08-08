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


def _crawl_run_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns("crawl_job_runs")}


def upgrade() -> None:
    if "llm_runtime_snapshot" in _crawl_run_columns():
        return

    with op.batch_alter_table("crawl_job_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("llm_runtime_snapshot", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if "llm_runtime_snapshot" not in _crawl_run_columns():
        return

    with op.batch_alter_table("crawl_job_runs", schema=None) as batch_op:
        batch_op.drop_column("llm_runtime_snapshot")
