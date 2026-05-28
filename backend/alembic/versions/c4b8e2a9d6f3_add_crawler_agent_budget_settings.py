"""add crawler agent budget settings

Revision ID: c4b8e2a9d6f3
Revises: 9a7c5e3d2b1f
Create Date: 2026-05-28 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c4b8e2a9d6f3"
down_revision: Union[str, Sequence[str], None] = "9a7c5e3d2b1f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "crawler_agent_max_chunks_per_run",
                sa.Integer(),
                server_default=sa.text("2"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "crawler_agent_max_tool_calls_per_run",
                sa.Integer(),
                server_default=sa.text("12"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.drop_column("crawler_agent_max_tool_calls_per_run")
        batch_op.drop_column("crawler_agent_max_chunks_per_run")