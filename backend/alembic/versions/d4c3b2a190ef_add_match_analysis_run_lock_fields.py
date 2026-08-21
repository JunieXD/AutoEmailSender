"""add match analysis run lock fields

Revision ID: d4c3b2a190ef
Revises: 7a9e2d4c6f81
Create Date: 2026-05-02 20:50:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4c3b2a190ef"
down_revision: Union[str, Sequence[str], None] = "7a9e2d4c6f81"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("match_analysis_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=32),
                server_default=sa.text("'failed'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("error_kind", sa.String(length=32), nullable=True)
        )

    op.execute(
        "UPDATE match_analysis_runs "
        "SET status = CASE WHEN success = 1 THEN 'succeeded' ELSE 'failed' END"
    )
    op.create_index(
        "uq_match_analysis_runs_running_per_task",
        "match_analysis_runs",
        ["email_task_id"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_match_analysis_runs_running_per_task", table_name="match_analysis_runs"
    )
    with op.batch_alter_table("match_analysis_runs") as batch_op:
        batch_op.drop_column("error_kind")
        batch_op.drop_column("finished_at")
        batch_op.drop_column("started_at")
        batch_op.drop_column("status")
