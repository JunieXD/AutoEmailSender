"""add identity next send after

Revision ID: 20260702_identity_next_send_after
Revises: 20260630_imap_efficiency_guards
Create Date: 2026-07-02 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260702_identity_next_send_after"
down_revision: Union[str, Sequence[str], None] = "20260630_imap_efficiency_guards"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _identity_profile_columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns("identity_profiles")}


def upgrade() -> None:
    if "next_send_after" in _identity_profile_columns():
        return

    with op.batch_alter_table("identity_profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("next_send_after", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if "next_send_after" not in _identity_profile_columns():
        return

    with op.batch_alter_table("identity_profiles", schema=None) as batch_op:
        batch_op.drop_column("next_send_after")
