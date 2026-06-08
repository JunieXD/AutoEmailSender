"""add concurrency guards

Revision ID: d0f1a2b3c4d5
Revises: c6d7e8f9a012
Create Date: 2026-05-10 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d0f1a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = "c6d7e8f9a012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_task_chain_tail_id(bind: sa.engine.Connection, task_id: int) -> int:
    current_id = task_id
    seen_ids = {task_id}
    while True:
        child_id = bind.scalar(
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


def _deduplicate_email_logs() -> None:
    op.execute(
        """
        DELETE FROM email_logs
        WHERE rfc_message_id IS NOT NULL
          AND id NOT IN (
            SELECT MIN(id)
            FROM email_logs
            WHERE rfc_message_id IS NOT NULL
            GROUP BY rfc_message_id
          )
        """,
    )


def _deduplicate_workspace_root_tasks() -> None:
    bind = op.get_bind()
    duplicate_groups = list(
        bind.execute(
            sa.text(
                """
                SELECT professor_id, identity_id, llm_profile_id
                FROM email_tasks
                WHERE source = 'manual'
                  AND batch_task_id IS NULL
                  AND parent_task_id IS NULL
                GROUP BY professor_id, identity_id, llm_profile_id
                HAVING COUNT(*) > 1
                """,
            ),
        ).mappings(),
    )

    for group in duplicate_groups:
        ids = [
            int(row["id"])
            for row in bind.execute(
                sa.text(
                    """
                    SELECT id
                    FROM email_tasks
                    WHERE source = 'manual'
                      AND batch_task_id IS NULL
                      AND parent_task_id IS NULL
                      AND professor_id = :professor_id
                      AND identity_id = :identity_id
                      AND llm_profile_id = :llm_profile_id
                    ORDER BY id
                    """,
                ),
                {
                    "professor_id": group["professor_id"],
                    "identity_id": group["identity_id"],
                    "llm_profile_id": group["llm_profile_id"],
                },
            ).mappings()
        ]
        if len(ids) <= 1:
            continue

        keep_id = ids[-1]
        duplicate_ids = ids[:-1]
        params = {f"id_{index}": value for index, value in enumerate(duplicate_ids)}
        id_list = ", ".join(f":id_{index}" for index in range(len(duplicate_ids)))
        duplicate_ids_with_children = {
            int(row["parent_task_id"])
            for row in bind.execute(
                sa.text(
                    f"""
                    SELECT DISTINCT parent_task_id
                    FROM email_tasks
                    WHERE parent_task_id IN ({id_list})
                    """
                ),
                params,
            ).mappings()
        }

        delete_ids = [task_id for task_id in duplicate_ids if task_id not in duplicate_ids_with_children]
        if delete_ids:
            delete_params = {f"id_{index}": value for index, value in enumerate(delete_ids)}
            delete_id_list = ", ".join(f":id_{index}" for index in range(len(delete_ids)))
            bind.execute(
                sa.text(f"UPDATE email_logs SET email_task_id = :keep_id WHERE email_task_id IN ({delete_id_list})"),
                {"keep_id": keep_id, **delete_params},
            )
            bind.execute(
                sa.text(f"DELETE FROM email_tasks WHERE id IN ({delete_id_list})"),
                delete_params,
            )

        parent_task_id = _get_task_chain_tail_id(bind, keep_id)
        for duplicate_id in duplicate_ids:
            if duplicate_id not in duplicate_ids_with_children:
                continue
            parent_task_id = _get_task_chain_tail_id(bind, parent_task_id)
            bind.execute(
                sa.text(
                    """
                    UPDATE email_tasks
                    SET parent_task_id = :parent_task_id,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :task_id
                    """
                ),
                {"parent_task_id": parent_task_id, "task_id": duplicate_id},
            )
            parent_task_id = duplicate_id


def upgrade() -> None:
    _deduplicate_email_logs()
    _deduplicate_workspace_root_tasks()

    op.create_index(
        "uq_email_logs_rfc_message_id",
        "email_logs",
        ["rfc_message_id"],
        unique=True,
    )
    op.create_index(
        "uq_email_tasks_workspace_task",
        "email_tasks",
        ["professor_id", "identity_id", "llm_profile_id"],
        unique=True,
        sqlite_where=sa.text("source = 'manual' AND batch_task_id IS NULL AND parent_task_id IS NULL"),
        postgresql_where=sa.text("source = 'manual' AND batch_task_id IS NULL AND parent_task_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_email_tasks_workspace_task", table_name="email_tasks")
    op.drop_index("uq_email_logs_rfc_message_id", table_name="email_logs")
