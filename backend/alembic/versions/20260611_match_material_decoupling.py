"""decouple match analysis material from email task material

Revision ID: 20260611matchmat
Revises: 20260609rewrite
Create Date: 2026-06-11 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260611matchmat"
down_revision: Union[str, Sequence[str], None] = "20260609rewrite"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRIMARY_MATERIAL_FILTER = """
(
    lower(identity_materials.original_filename) LIKE '%.pdf'
    OR lower(identity_materials.original_filename) LIKE '%.docx'
    OR lower(identity_materials.original_filename) LIKE '%.txt'
    OR lower(identity_materials.original_filename) LIKE '%.md'
)
"""


def upgrade() -> None:
    with op.batch_alter_table("match_analysis_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("primary_material_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_match_analysis_runs_primary_material_id_identity_materials",
            "identity_materials",
            ["primary_material_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_match_analysis_runs_primary_material_id",
            ["primary_material_id"],
        )

    op.execute(
        f"""
        UPDATE match_analysis_runs
        SET primary_material_id = (
            SELECT email_tasks.primary_material_id
            FROM email_tasks
            JOIN identity_materials ON identity_materials.id = email_tasks.primary_material_id
            WHERE email_tasks.id = match_analysis_runs.email_task_id
              AND email_tasks.identity_id = match_analysis_runs.identity_id
              AND identity_materials.identity_id = match_analysis_runs.identity_id
              AND {PRIMARY_MATERIAL_FILTER}
        )
        WHERE primary_material_id IS NULL
        """
    )

    op.execute(
        f"""
        UPDATE identity_profiles
        SET current_primary_material_id = (
            SELECT email_tasks.primary_material_id
            FROM email_tasks
            JOIN identity_materials ON identity_materials.id = email_tasks.primary_material_id
            WHERE email_tasks.identity_id = identity_profiles.id
              AND identity_materials.identity_id = identity_profiles.id
              AND {PRIMARY_MATERIAL_FILTER}
            ORDER BY email_tasks.updated_at DESC, email_tasks.created_at DESC, email_tasks.id DESC
            LIMIT 1
        )
        WHERE current_primary_material_id IS NULL
          AND EXISTS (
              SELECT 1
              FROM email_tasks
              JOIN identity_materials ON identity_materials.id = email_tasks.primary_material_id
              WHERE email_tasks.identity_id = identity_profiles.id
                AND identity_materials.identity_id = identity_profiles.id
                AND {PRIMARY_MATERIAL_FILTER}
          )
        """
    )

    op.execute(
        f"""
        UPDATE identity_profiles
        SET current_primary_material_id = (
            SELECT identity_materials.id
            FROM identity_materials
            WHERE identity_materials.identity_id = identity_profiles.id
              AND {PRIMARY_MATERIAL_FILTER}
            ORDER BY identity_materials.created_at DESC, identity_materials.id DESC
            LIMIT 1
        )
        WHERE current_primary_material_id IS NULL
          AND (
              SELECT count(*)
              FROM identity_materials
              WHERE identity_materials.identity_id = identity_profiles.id
                AND {PRIMARY_MATERIAL_FILTER}
          ) = 1
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("match_analysis_runs", schema=None) as batch_op:
        batch_op.drop_index("ix_match_analysis_runs_primary_material_id")
        batch_op.drop_constraint(
            "fk_match_analysis_runs_primary_material_id_identity_materials",
            type_="foreignkey",
        )
        batch_op.drop_column("primary_material_id")
