"""add email delivery management state

Revision ID: 20260807_email_delivery_management
Revises: 20260807_scheduler_leases
Create Date: 2026-08-07 10:30:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260807_email_delivery_management"
down_revision: Union[str, Sequence[str], None] = "20260807_scheduler_leases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _email_task_columns() -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("email_tasks")
    }


def _email_task_indexes() -> set[str]:
    return {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("email_tasks")
    }


def upgrade() -> None:
    columns = _email_task_columns()
    missing_columns = {
        "last_scheduled_at",
        "schedule_canceled_at",
    } - columns
    if missing_columns:
        with op.batch_alter_table("email_tasks", schema=None) as batch_op:
            if "last_scheduled_at" in missing_columns:
                batch_op.add_column(
                    sa.Column(
                        "last_scheduled_at",
                        sa.DateTime(timezone=True),
                        nullable=True,
                    ),
                )
            if "schedule_canceled_at" in missing_columns:
                batch_op.add_column(
                    sa.Column(
                        "schedule_canceled_at",
                        sa.DateTime(timezone=True),
                        nullable=True,
                    ),
                )

    indexes = _email_task_indexes()
    if "ix_email_tasks_schedule_canceled_at" not in indexes:
        op.create_index(
            "ix_email_tasks_schedule_canceled_at",
            "email_tasks",
            ["schedule_canceled_at"],
            unique=False,
            sqlite_where=sa.text("schedule_canceled_at IS NOT NULL"),
            postgresql_where=sa.text("schedule_canceled_at IS NOT NULL"),
        )
    if "ix_email_tasks_delivery_upcoming_schedule" not in indexes:
        op.create_index(
            "ix_email_tasks_delivery_upcoming_schedule",
            "email_tasks",
            ["scheduled_at", "id"],
            unique=False,
            sqlite_where=sa.text(
                "schedule_canceled_at IS NULL AND batch_send_canceled_at IS NULL"
            ),
            postgresql_where=sa.text(
                "schedule_canceled_at IS NULL AND batch_send_canceled_at IS NULL"
            ),
        )
    if "ix_email_tasks_delivery_attention_updated" not in indexes:
        op.create_index(
            "ix_email_tasks_delivery_attention_updated",
            "email_tasks",
            ["updated_at", "id"],
            unique=False,
            sqlite_where=sa.text(
                "schedule_canceled_at IS NULL AND batch_send_canceled_at IS NULL"
            ),
            postgresql_where=sa.text(
                "schedule_canceled_at IS NULL AND batch_send_canceled_at IS NULL"
            ),
        )


def downgrade() -> None:
    indexes = _email_task_indexes()
    for index_name in (
        "ix_email_tasks_delivery_attention_updated",
        "ix_email_tasks_delivery_upcoming_schedule",
        "ix_email_tasks_schedule_canceled_at",
    ):
        if index_name in indexes:
            op.drop_index(index_name, table_name="email_tasks")

    columns = _email_task_columns()
    if {"schedule_canceled_at", "last_scheduled_at"}.intersection(columns):
        with op.batch_alter_table("email_tasks", schema=None) as batch_op:
            if "schedule_canceled_at" in columns:
                batch_op.drop_column("schedule_canceled_at")
            if "last_scheduled_at" in columns:
                batch_op.drop_column("last_scheduled_at")
