"""add identity outreach templates

Revision ID: ab12c34d56ef
Revises: f14c0e8d3b7a
Create Date: 2026-04-20 11:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ab12c34d56ef"
down_revision: Union[str, Sequence[str], None] = "f14c0e8d3b7a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("identity_profiles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "outreach_generation_mode",
                sa.String(length=20),
                server_default=sa.text("'llm'"),
                nullable=False,
            ),
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
    with op.batch_alter_table("identity_profiles", schema=None) as batch_op:
        batch_op.drop_column("outreach_template_body_html")
        batch_op.drop_column("outreach_template_body_text")
        batch_op.drop_column("outreach_template_subject")
        batch_op.drop_column("outreach_generation_mode")
