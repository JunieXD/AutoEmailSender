"""add crawl run concurrency metrics

Revision ID: e5f1c2d3a4b6
Revises: d4c3b2a190ef
Create Date: 2026-05-02 22:20:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f1c2d3a4b6"
down_revision: Union[str, Sequence[str], None] = "d4c3b2a190ef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("crawl_job_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False
            )
        )
        batch_op.add_column(
            sa.Column(
                "host_limited_count",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "failed_candidate_count",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "unchanged_candidate_count",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("crawl_job_runs") as batch_op:
        batch_op.drop_column("unchanged_candidate_count")
        batch_op.drop_column("failed_candidate_count")
        batch_op.drop_column("host_limited_count")
        batch_op.drop_column("retry_count")
