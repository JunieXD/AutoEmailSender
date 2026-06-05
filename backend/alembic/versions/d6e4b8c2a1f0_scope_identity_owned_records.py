"""scope identity owned records

Revision ID: d6e4b8c2a1f0
Revises: c4b8e2a9d6f3, b2e7c9f1a4d6
Create Date: 2026-06-01 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d6e4b8c2a1f0"
down_revision: Union[str, Sequence[str], None] = ("c4b8e2a9d6f3", "b2e7c9f1a4d6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


WORKSPACE_TASK_WHERE = "source = 'manual' AND batch_task_id IS NULL AND parent_task_id IS NULL"


def upgrade() -> None:
    op.create_table(
        "app_metadata",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )

    connection = op.get_bind()
    duplicate_rows = connection.execute(
        sa.text(
            """
            SELECT professor_id, identity_id
            FROM email_tasks
            WHERE source = 'manual'
              AND batch_task_id IS NULL
              AND parent_task_id IS NULL
            GROUP BY professor_id, identity_id
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()

    for professor_id, identity_id in duplicate_rows:
        task_ids = [
            row[0]
            for row in connection.execute(
                sa.text(
                    """
                    SELECT id
                    FROM email_tasks
                    WHERE professor_id = :professor_id
                      AND identity_id = :identity_id
                      AND source = 'manual'
                      AND batch_task_id IS NULL
                      AND parent_task_id IS NULL
                    ORDER BY created_at DESC, id DESC
                    """
                ),
                {"professor_id": professor_id, "identity_id": identity_id},
            ).fetchall()
        ]
        for duplicate_task_id in task_ids[1:]:
            connection.execute(
                sa.text(
                    """
                    UPDATE email_tasks
                    SET parent_task_id = :parent_task_id,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :task_id
                    """
                ),
                {"task_id": duplicate_task_id, "parent_task_id": task_ids[0]},
            )

    op.drop_index("uq_email_tasks_workspace_task", table_name="email_tasks")
    op.create_index(
        "uq_email_tasks_workspace_task",
        "email_tasks",
        ["professor_id", "identity_id"],
        unique=True,
        sqlite_where=sa.text(WORKSPACE_TASK_WHERE),
    )


def downgrade() -> None:
    op.drop_table("app_metadata")

    op.drop_index("uq_email_tasks_workspace_task", table_name="email_tasks")
    op.create_index(
        "uq_email_tasks_workspace_task",
        "email_tasks",
        ["professor_id", "identity_id", "llm_profile_id"],
        unique=True,
        sqlite_where=sa.text(WORKSPACE_TASK_WHERE),
    )
