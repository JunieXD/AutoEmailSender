"""add professor tags

Revision ID: 20260606tags
Revises: d6e4b8c2a1f0
Create Date: 2026-06-06 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260606tags"
down_revision: Union[str, Sequence[str], None] = "d6e4b8c2a1f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_TAGS = [
    ("已退休", "#92400e", "#fef3c7"),
    ("高意愿", "#166534", "#dcfce7"),
    ("低意愿", "#991b1b", "#fee2e2"),
    ("羊导", "#6b21a8", "#f3e8ff"),
    ("高强度", "#1e40af", "#dbeafe"),
]


def upgrade() -> None:
    op.create_table(
        "professor_tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("text_color", sa.String(length=16), nullable=False),
        sa.Column("background_color", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_professor_tags")),
        sa.UniqueConstraint("name", name=op.f("uq_professor_tags_name")),
    )
    op.create_table(
        "professor_tag_links",
        sa.Column("professor_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["professor_id"],
            ["professors.id"],
            name=op.f("fk_professor_tag_links_professor_id_professors"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["professor_tags.id"],
            name=op.f("fk_professor_tag_links_tag_id_professor_tags"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "professor_id",
            "tag_id",
            name=op.f("pk_professor_tag_links"),
        ),
        sa.UniqueConstraint(
            "professor_id",
            "tag_id",
            name="uq_professor_tag_links_professor_tag",
        ),
    )
    op.create_index(
        op.f("ix_professor_tag_links_tag_id"),
        "professor_tag_links",
        ["tag_id"],
        unique=False,
    )

    tag_table = sa.table(
        "professor_tags",
        sa.column("name", sa.String),
        sa.column("text_color", sa.String),
        sa.column("background_color", sa.String),
    )
    op.bulk_insert(
        tag_table,
        [
            {
                "name": name,
                "text_color": text_color,
                "background_color": background_color,
            }
            for name, text_color, background_color in DEFAULT_TAGS
        ],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_professor_tag_links_tag_id"),
        table_name="professor_tag_links",
    )
    op.drop_table("professor_tag_links")
    op.drop_table("professor_tags")
