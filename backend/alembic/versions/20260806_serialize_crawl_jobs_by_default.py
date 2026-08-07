"""serialize crawl jobs by default

Revision ID: 20260806_crawl_job_serial
Revises: 20260805_merge_match_fallback
Create Date: 2026-08-06 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260806_crawl_job_serial"
down_revision: Union[str, Sequence[str], None] = "20260805_merge_match_fallback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Value 2 was the historical default. Preserve explicit non-default choices.
    op.execute(
        "UPDATE app_settings SET crawler_worker_count = 1 "
        "WHERE crawler_worker_count = 2"
    )
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.alter_column(
            "crawler_worker_count",
            existing_type=sa.Integer(),
            server_default=sa.text("1"),
            existing_nullable=False,
        )


def downgrade() -> None:
    op.execute(
        "UPDATE app_settings SET crawler_worker_count = 2 "
        "WHERE crawler_worker_count = 1"
    )
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.alter_column(
            "crawler_worker_count",
            existing_type=sa.Integer(),
            server_default=sa.text("2"),
            existing_nullable=False,
        )
