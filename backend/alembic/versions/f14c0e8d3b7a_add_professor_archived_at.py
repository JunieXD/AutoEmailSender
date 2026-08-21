"""add professor archived_at

Revision ID: f14c0e8d3b7a
Revises: e3f9a7c1b2d4
Create Date: 2026-04-19 23:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f14c0e8d3b7a"
down_revision: Union[str, Sequence[str], None] = "e3f9a7c1b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("professors", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("professors", schema=None) as batch_op:
        batch_op.drop_column("archived_at")
