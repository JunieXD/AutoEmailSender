"""associate crawl enrichment tasks with their operation

Revision ID: 20260822_enrichment_task_op
Revises: 20260819_cleanup_public_beta
Create Date: 2026-08-22 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_enrichment_task_op"
down_revision: str | Sequence[str] | None = "20260819_cleanup_public_beta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _task_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        str(column["name"])
        for column in inspector.get_columns("crawl_candidate_enrichment_tasks")
    }


def upgrade() -> None:
    if "enrichment_operation_id" in _task_columns():
        return

    with op.batch_alter_table(
        "crawl_candidate_enrichment_tasks", schema=None
    ) as batch_op:
        batch_op.add_column(
            sa.Column(
                "enrichment_operation_id",
                sa.String(length=36),
                nullable=True,
            )
        )


def downgrade() -> None:
    if "enrichment_operation_id" not in _task_columns():
        return

    with op.batch_alter_table(
        "crawl_candidate_enrichment_tasks", schema=None
    ) as batch_op:
        batch_op.drop_column("enrichment_operation_id")
