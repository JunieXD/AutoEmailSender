"""add workspace draft rewrite fields

Revision ID: 20260609rewrite
Revises: 20260606tagorder
Create Date: 2026-06-09 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260609rewrite"
down_revision: Union[str, Sequence[str], None] = "20260606tagorder"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "draft_generation_started_at", sa.DateTime(timezone=True), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column("draft_rewrite_source_subject", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("draft_rewrite_source_body_text", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("draft_rewrite_source_body_html", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "draft_rewrite_source_selected_material_ids", sa.JSON(), nullable=True
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.drop_column("draft_rewrite_source_selected_material_ids")
        batch_op.drop_column("draft_rewrite_source_body_html")
        batch_op.drop_column("draft_rewrite_source_body_text")
        batch_op.drop_column("draft_rewrite_source_subject")
        batch_op.drop_column("draft_generation_started_at")
