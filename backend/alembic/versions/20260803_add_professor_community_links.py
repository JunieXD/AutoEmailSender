"""add stable professor community record links

Revision ID: 20260803_community_links
Revises: 20260730_db_performance
Create Date: 2026-08-03 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260803_community_links"
down_revision: Union[str, Sequence[str], None] = "20260730_db_performance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "professor_community_links",
        sa.Column("professor_id", sa.Integer(), nullable=False),
        sa.Column("community_record_id", sa.String(length=80), nullable=False),
        sa.Column("dataset_version", sa.String(length=128), nullable=False),
        sa.Column("imported_snapshot_json", sa.JSON(), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "last_checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "remote_status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("remote_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["professor_id"],
            ["professors.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("professor_id"),
        sa.UniqueConstraint(
            "community_record_id",
            name="uq_professor_community_links_record_id",
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_professor_community_links_remote_status",
        "professor_community_links",
        ["remote_status"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_professor_community_links_remote_status",
        table_name="professor_community_links",
        if_exists=True,
    )
    op.drop_table("professor_community_links", if_exists=True)
