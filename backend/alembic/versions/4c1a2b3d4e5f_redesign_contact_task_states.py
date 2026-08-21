"""redesign contact task states

Revision ID: 4c1a2b3d4e5f
Revises: 2f6a9d8c1e20
Create Date: 2026-04-25 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4c1a2b3d4e5f"
down_revision: Union[str, Sequence[str], None] = "2f6a9d8c1e20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_task_state_redesign_backup",
        sa.Column("email_task_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=False),
        if_not_exists=True,
    )
    op.execute(
        """
        INSERT OR IGNORE INTO email_task_state_redesign_backup (email_task_id, previous_status)
        SELECT id, status
        FROM email_tasks
        WHERE status = 'skipped'
        """,
    )

    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "source",
                sa.String(length=20),
                server_default=sa.text("'manual'"),
                nullable=True,
            ),
        )
        batch_op.add_column(sa.Column("parent_task_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("cancellation_reason", sa.String(length=32), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_email_tasks_parent_task_id_email_tasks",
            "email_tasks",
            ["parent_task_id"],
            ["id"],
        )
        batch_op.create_unique_constraint(
            "uq_email_tasks_parent_task_id",
            ["parent_task_id"],
        )

    op.execute(
        """
        UPDATE email_tasks
        SET status = CASE
                WHEN status = 'skipped' AND EXISTS (
                    SELECT 1
                    FROM batch_tasks
                    WHERE batch_tasks.id = email_tasks.batch_task_id
                      AND batch_tasks.status = 'stopped'
                ) THEN 'canceled'
                WHEN status = 'skipped' THEN 'matched'
                ELSE status
            END,
            cancellation_reason = CASE
                WHEN status = 'skipped' AND EXISTS (
                    SELECT 1
                    FROM batch_tasks
                    WHERE batch_tasks.id = email_tasks.batch_task_id
                      AND batch_tasks.status = 'stopped'
                ) THEN 'batch_stopped'
                ELSE cancellation_reason
            END,
            source = CASE
                WHEN batch_task_id IS NOT NULL THEN 'batch'
                ELSE 'manual'
            END
        """,
    )

    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.alter_column(
            "source",
            existing_type=sa.String(length=20),
            server_default=sa.text("'manual'"),
            nullable=False,
        )


def downgrade() -> None:
    op.execute(
        """
        UPDATE email_tasks
        SET status = (
            SELECT previous_status
            FROM email_task_state_redesign_backup
            WHERE email_task_id = email_tasks.id
        ),
            cancellation_reason = NULL
        WHERE id IN (
            SELECT email_task_id
            FROM email_task_state_redesign_backup
        )
        """,
    )
    op.execute(
        """
        UPDATE email_tasks
        SET status = 'skipped',
            cancellation_reason = NULL
        WHERE status = 'canceled'
          AND cancellation_reason = 'batch_stopped'
        """,
    )

    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.drop_constraint("uq_email_tasks_parent_task_id", type_="unique")
        batch_op.drop_constraint(
            "fk_email_tasks_parent_task_id_email_tasks", type_="foreignkey"
        )
        batch_op.drop_column("cancellation_reason")
        batch_op.drop_column("parent_task_id")
        batch_op.drop_column("source")

    op.drop_table("email_task_state_redesign_backup")
