"""raise draft max tokens default

Revision ID: e8f2a4b6c9d0
Revises: d7a8c9e1f2b3
Create Date: 2026-05-07 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8f2a4b6c9d0"
down_revision: Union[str, Sequence[str], None] = "d7a8c9e1f2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE app_settings SET draft_max_tokens = 6000 WHERE draft_max_tokens = 3600"
    )
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.alter_column(
            "draft_max_tokens",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default=sa.text("6000"),
        )


def downgrade() -> None:
    op.execute(
        "UPDATE app_settings SET draft_max_tokens = 3600 WHERE draft_max_tokens = 6000"
    )
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.alter_column(
            "draft_max_tokens",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default=sa.text("3600"),
        )
