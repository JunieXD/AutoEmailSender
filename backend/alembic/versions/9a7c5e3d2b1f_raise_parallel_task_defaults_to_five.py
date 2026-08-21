"""raise parallel task defaults to five

Revision ID: 9a7c5e3d2b1f
Revises: d2c4e6f8a0b1
Create Date: 2026-05-27 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "9a7c5e3d2b1f"
down_revision: Union[str, Sequence[str], None] = "d2c4e6f8a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONCURRENCY_COLUMNS = (
    "match_analysis_job_item_concurrency",
    "batch_draft_generation_concurrency",
)


def upgrade() -> None:
    for column_name in CONCURRENCY_COLUMNS:
        op.execute(
            sa.text(
                f"UPDATE app_settings SET {column_name} = 5 WHERE {column_name} = 3"
            )
        )
        with op.batch_alter_table("app_settings", schema=None) as batch_op:
            batch_op.alter_column(
                column_name,
                existing_type=sa.Integer(),
                server_default=sa.text("5"),
                existing_nullable=False,
            )


def downgrade() -> None:
    for column_name in CONCURRENCY_COLUMNS:
        op.execute(
            sa.text(
                f"UPDATE app_settings SET {column_name} = 3 WHERE {column_name} = 5"
            )
        )
        with op.batch_alter_table("app_settings", schema=None) as batch_op:
            batch_op.alter_column(
                column_name,
                existing_type=sa.Integer(),
                server_default=sa.text("3"),
                existing_nullable=False,
            )
