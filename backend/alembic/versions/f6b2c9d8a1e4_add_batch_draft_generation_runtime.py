"""add batch draft generation runtime

Revision ID: f6b2c9d8a1e4
Revises: e8f2a4b6c9d0
Create Date: 2026-05-07 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6b2c9d8a1e4"
down_revision: Union[str, Sequence[str], None] = "e8f2a4b6c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "batch_draft_generation_concurrency",
                sa.Integer(),
                server_default=sa.text("5"),
                nullable=False,
            ),
        )
    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "draft_generation_previous_status", sa.String(length=32), nullable=True
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.drop_column("draft_generation_previous_status")
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.drop_column("batch_draft_generation_concurrency")
