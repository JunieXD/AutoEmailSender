"""snapshot the app version on crawl runs

Revision ID: 20260803_crawl_run_app_version
Revises: 20260803_agent_action_plans
Create Date: 2026-08-03 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260803_crawl_run_app_version"
down_revision: Union[str, Sequence[str], None] = "20260803_agent_action_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _crawl_run_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns("crawl_job_runs")}


def upgrade() -> None:
    if "app_version" in _crawl_run_columns():
        return

    with op.batch_alter_table("crawl_job_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("app_version", sa.String(length=32), nullable=True)
        )


def downgrade() -> None:
    if "app_version" not in _crawl_run_columns():
        return

    with op.batch_alter_table("crawl_job_runs", schema=None) as batch_op:
        batch_op.drop_column("app_version")
