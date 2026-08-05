"""record template fallback provenance for batch drafts

Revision ID: 20260805_batch_draft_fallback
Revises: 20260804_merge_agent_change_recent_papers
Create Date: 2026-08-05 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260805_batch_draft_fallback"
down_revision: Union[str, Sequence[str], None] = (
    "20260804_merge_agent_change_recent_papers"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _email_task_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns("email_tasks")}


def upgrade() -> None:
    columns = _email_task_columns()
    if {
        "draft_generation_source",
        "draft_fallback_reason",
    }.issubset(columns):
        return

    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        if "draft_generation_source" not in columns:
            batch_op.add_column(
                sa.Column("draft_generation_source", sa.String(length=32), nullable=True)
            )
        if "draft_fallback_reason" not in columns:
            batch_op.add_column(
                sa.Column("draft_fallback_reason", sa.String(length=64), nullable=True)
            )


def downgrade() -> None:
    columns = _email_task_columns()
    if not {
        "draft_generation_source",
        "draft_fallback_reason",
    }.intersection(columns):
        return

    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        if "draft_fallback_reason" in columns:
            batch_op.drop_column("draft_fallback_reason")
        if "draft_generation_source" in columns:
            batch_op.drop_column("draft_generation_source")
