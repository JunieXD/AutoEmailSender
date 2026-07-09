"""add professor dashboard query indexes

Revision ID: 20260709_professor_dashboard_indexes
Revises: 20260708_merge_email_history_direction_heads
Create Date: 2026-07-09 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260709_professor_dashboard_indexes"
down_revision: Union[str, Sequence[str], None] = "20260708_merge_email_history_direction_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_professors_archived_created_id",
        "professors",
        ["archived_at", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_email_tasks_identity_professor_created_id",
        "email_tasks",
        ["identity_id", "professor_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_email_logs_status_identity_professor_direction_created",
        "email_logs",
        ["identity_id", "professor_id", "direction", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_logs_status_identity_professor_direction_created",
        table_name="email_logs",
    )
    op.drop_index(
        "ix_email_tasks_identity_professor_created_id",
        table_name="email_tasks",
    )
    op.drop_index(
        "ix_professors_archived_created_id",
        table_name="professors",
    )
