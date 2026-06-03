"""add runtime concurrency settings

Revision ID: b6f1a2c3d4e5
Revises: a9c8e7d6f5b4
Create Date: 2026-05-04 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b6f1a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "a9c8e7d6f5b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "match_analysis_job_worker_count",
                sa.Integer(),
                server_default=sa.text("1"),
                nullable=False,
            ),
        )
        batch_op.add_column(
            sa.Column(
                "match_analysis_job_item_concurrency",
                sa.Integer(),
                server_default=sa.text("5"),
                nullable=False,
            ),
        )
        batch_op.add_column(
            sa.Column(
                "match_analysis_job_interval_seconds",
                sa.Integer(),
                server_default=sa.text("10"),
                nullable=False,
            ),
        )
        batch_op.add_column(
            sa.Column(
                "crawler_worker_count",
                sa.Integer(),
                server_default=sa.text("2"),
                nullable=False,
            ),
        )
        batch_op.add_column(
            sa.Column(
                "crawler_profile_enrichment_concurrency",
                sa.Integer(),
                server_default=sa.text("3"),
                nullable=False,
            ),
        )
        batch_op.add_column(
            sa.Column(
                "crawler_host_concurrency",
                sa.Integer(),
                server_default=sa.text("2"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.drop_column("crawler_host_concurrency")
        batch_op.drop_column("crawler_profile_enrichment_concurrency")
        batch_op.drop_column("crawler_worker_count")
        batch_op.drop_column("match_analysis_job_interval_seconds")
        batch_op.drop_column("match_analysis_job_item_concurrency")
        batch_op.drop_column("match_analysis_job_worker_count")
