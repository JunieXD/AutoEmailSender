"""allow match analysis runs without email tasks

Revision ID: 20260807_match_task_decoupling
Revises: 20260807_email_delivery_management
Create Date: 2026-08-07 18:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260807_match_task_decoupling"
down_revision: Union[str, Sequence[str], None] = (
    "20260807_email_delivery_management"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _email_task_id_nullable() -> bool:
    columns = {
        column["name"]: column
        for column in sa.inspect(op.get_bind()).get_columns("match_analysis_runs")
    }
    email_task_id = columns.get("email_task_id")
    if email_task_id is None:
        raise RuntimeError("match_analysis_runs.email_task_id 不存在，无法执行迁移")
    return email_task_id.get("nullable") is True


def upgrade() -> None:
    if _email_task_id_nullable():
        return
    with op.batch_alter_table("match_analysis_runs", schema=None) as batch_op:
        batch_op.alter_column(
            "email_task_id",
            existing_type=sa.Integer(),
            existing_nullable=False,
            nullable=True,
        )


def downgrade() -> None:
    if not _email_task_id_nullable():
        return
    runs = sa.table(
        "match_analysis_runs",
        sa.column("email_task_id", sa.Integer()),
    )
    detached_run_count = op.get_bind().scalar(
        sa.select(sa.func.count()).select_from(runs).where(
            runs.c.email_task_id.is_(None)
        )
    )
    if detached_run_count:
        raise RuntimeError(
            "存在不关联邮件任务的匹配分析记录，无法安全降级；请先升级回当前版本"
        )
    with op.batch_alter_table("match_analysis_runs", schema=None) as batch_op:
        batch_op.alter_column(
            "email_task_id",
            existing_type=sa.Integer(),
            existing_nullable=True,
            nullable=False,
        )
