"""add email task outreach snapshots

Revision ID: d91f2a4b8c7e
Revises: ab12c34d56ef
Create Date: 2026-04-20 16:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d91f2a4b8c7e"
down_revision: Union[str, Sequence[str], None] = "ab12c34d56ef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("outreach_generation_mode", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(
            sa.Column("outreach_template_subject", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column("outreach_template_body_text", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("outreach_template_body_html", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.drop_column("outreach_template_body_html")
        batch_op.drop_column("outreach_template_body_text")
        batch_op.drop_column("outreach_template_subject")
        batch_op.drop_column("outreach_generation_mode")
