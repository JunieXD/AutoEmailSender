"""add identity profile and sender names

Revision ID: 2f6a9d8c1e20
Revises: 9c3d5b4a7f21
Create Date: 2026-04-24 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2f6a9d8c1e20"
down_revision: Union[str, Sequence[str], None] = "9c3d5b4a7f21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("identity_profiles") as batch_op:
        batch_op.add_column(
            sa.Column("profile_name", sa.String(length=100), nullable=True)
        )
        batch_op.add_column(
            sa.Column("sender_name", sa.String(length=100), nullable=True)
        )

    op.execute(
        """
        UPDATE identity_profiles
        SET profile_name = COALESCE(NULLIF(profile_name, ''), name),
            sender_name = COALESCE(NULLIF(sender_name, ''), name)
        """,
    )

    with op.batch_alter_table("identity_profiles") as batch_op:
        batch_op.alter_column(
            "profile_name", existing_type=sa.String(length=100), nullable=False
        )
        batch_op.alter_column(
            "sender_name", existing_type=sa.String(length=100), nullable=False
        )


def downgrade() -> None:
    with op.batch_alter_table("identity_profiles") as batch_op:
        batch_op.drop_column("sender_name")
        batch_op.drop_column("profile_name")
