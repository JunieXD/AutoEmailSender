"""add professor personal note

Revision ID: 20260612profnote
Revises: 20260611matchmat
Create Date: 2026-06-12 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260612profnote"
down_revision: Union[str, Sequence[str], None] = "20260611matchmat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("professors", schema=None) as batch_op:
        batch_op.add_column(sa.Column("personal_note", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("professors", schema=None) as batch_op:
        batch_op.drop_column("personal_note")
