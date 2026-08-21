"""drop legacy material fields

Revision ID: c8d7e1a42b90
Revises: b1f4f0d34c6a
Create Date: 2026-04-19 13:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8d7e1a42b90"
down_revision: Union[str, Sequence[str], None] = "b1f4f0d34c6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.drop_column("selected_attachments")

    with op.batch_alter_table("batch_tasks", schema=None) as batch_op:
        batch_op.drop_column("selected_attachment_ids")

    with op.batch_alter_table("identity_profiles", schema=None) as batch_op:
        batch_op.drop_column("resume_text")
        batch_op.drop_column("resume_file_path")

    op.drop_table("attachment_assets")


def downgrade() -> None:
    op.create_table(
        "attachment_assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["identity_id"], ["identity_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_attachment_assets_identity_id"),
        "attachment_assets",
        ["identity_id"],
        unique=False,
    )

    with op.batch_alter_table("identity_profiles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("resume_file_path", sa.String(length=500), nullable=True)
        )
        batch_op.add_column(sa.Column("resume_text", sa.Text(), nullable=True))

    with op.batch_alter_table("batch_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("selected_attachment_ids", sa.JSON(), nullable=True)
        )

    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("selected_attachments", sa.JSON(), nullable=True))
