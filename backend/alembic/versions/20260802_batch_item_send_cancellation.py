"""add reversible batch item send cancellation

Revision ID: 20260802_batch_send_cancel
Revises: 20260802_batch_template_snapshot
Create Date: 2026-08-02 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260802_batch_send_cancel"
down_revision: Union[str, Sequence[str], None] = "20260802_batch_template_snapshot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _email_task_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns("email_tasks")}


def upgrade() -> None:
    if "batch_send_canceled_at" in _email_task_columns():
        return

    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("batch_send_canceled_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if "batch_send_canceled_at" not in _email_task_columns():
        return

    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.drop_column("batch_send_canceled_at")
