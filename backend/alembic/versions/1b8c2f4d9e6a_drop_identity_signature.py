"""drop identity signature

Revision ID: 1b8c2f4d9e6a
Revises: 7a1d5e42c9bd
Create Date: 2026-04-19 10:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1b8c2f4d9e6a"
down_revision: Union[str, Sequence[str], None] = "7a1d5e42c9bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {
        column["name"] for column in inspector.get_columns("identity_profiles")
    }
    if "signature" not in existing_columns:
        return

    with op.batch_alter_table("identity_profiles", schema=None) as batch_op:
        batch_op.drop_column("signature")


def downgrade() -> None:
    with op.batch_alter_table("identity_profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("signature", sa.Text(), nullable=True))
