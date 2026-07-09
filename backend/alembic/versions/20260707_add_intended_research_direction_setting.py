"""add intended research direction setting

Revision ID: 20260707_intended_direction
Revises: 20260703_imap_folder_history_scan
Create Date: 2026-07-07 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260707_intended_direction"
down_revision: Union[str, Sequence[str], None] = "20260703_imap_folder_history_scan"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "intended_research_direction" in _app_setting_columns():
        return

    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "intended_research_direction",
                sa.Text(),
                server_default=sa.text("''"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    if "intended_research_direction" not in _app_setting_columns():
        return

    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.drop_column("intended_research_direction")


def _app_setting_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns("app_settings")}
