"""add professor tag sort order

Revision ID: 20260606tagorder
Revises: 20260606tags
Create Date: 2026-06-06 00:00:01.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260606tagorder"
down_revision: Union[str, Sequence[str], None] = "20260606tags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "professor_tag_links",
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT professor_id, tag_id
            FROM professor_tag_links
            ORDER BY professor_id ASC, created_at ASC, tag_id ASC
            """,
        ),
    ).fetchall()
    current_professor_id: int | None = None
    current_order = 0
    for row in rows:
        if current_professor_id != row.professor_id:
            current_professor_id = row.professor_id
            current_order = 0
        connection.execute(
            sa.text(
                """
                UPDATE professor_tag_links
                SET sort_order = :sort_order
                WHERE professor_id = :professor_id AND tag_id = :tag_id
                """,
            ),
            {
                "sort_order": current_order,
                "professor_id": row.professor_id,
                "tag_id": row.tag_id,
            },
        )
        current_order += 1


def downgrade() -> None:
    op.drop_column("professor_tag_links", "sort_order")
