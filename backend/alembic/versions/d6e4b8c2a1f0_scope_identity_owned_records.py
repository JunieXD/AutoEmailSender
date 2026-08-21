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


WORKSPACE_TASK_WHERE = (
    "source = 'manual' AND batch_task_id IS NULL AND parent_task_id IS NULL"
)


def _get_task_chain_tail_id(connection: sa.engine.Connection, task_id: int) -> int:
    current_id = task_id
    seen_ids = {task_id}
    while True:
        child_id = connection.scalar(
            sa.text(
                """
                SELECT id
                FROM email_tasks
                WHERE parent_task_id = :parent_task_id
                ORDER BY id
                LIMIT 1
                """
            ),
            {"parent_task_id": current_id},
        )
        if child_id is None:
            return current_id
        current_id = int(child_id)
        if current_id in seen_ids:
            raise RuntimeError(f"检测到重复工作区任务链循环：task_id={task_id}")
        seen_ids.add(current_id)


def _index_exists(connection: sa.engine.Connection, index_name: str) -> bool:
    return bool(
        connection.scalar(
            sa.text(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'index' AND name = :index_name
                """
            ),
            {"index_name": index_name},
        )
    )


def upgrade() -> None:
    op.create_table(
        "app_metadata",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        if_not_exists=True,
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
        parent_task_id = task_ids[0]
        for duplicate_task_id in task_ids[1:]:
            parent_task_id = _get_task_chain_tail_id(connection, parent_task_id)
            connection.execute(
                sa.text(
                    """
                    UPDATE email_tasks
                    SET parent_task_id = :parent_task_id,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :task_id
                    """
                ),
                {"task_id": duplicate_task_id, "parent_task_id": parent_task_id},
            )
            parent_task_id = duplicate_task_id

    if _index_exists(connection, "uq_email_tasks_workspace_task"):
        op.drop_index("uq_email_tasks_workspace_task", table_name="email_tasks")
    if not _index_exists(connection, "uq_email_tasks_workspace_task"):
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
