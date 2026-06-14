"""backfill task primary materials from identity defaults

Revision ID: 20260614taskmat
Revises: 20260612profnote
Create Date: 2026-06-14 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260614taskmat"
down_revision: Union[str, Sequence[str], None] = "20260612profnote"
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
    op.execute(
        f"""
        UPDATE email_tasks
        SET primary_material_id = (
            SELECT identity_profiles.current_primary_material_id
            FROM identity_profiles
            JOIN identity_materials
              ON identity_materials.id = identity_profiles.current_primary_material_id
            WHERE identity_profiles.id = email_tasks.identity_id
              AND identity_materials.identity_id = email_tasks.identity_id
              AND {PRIMARY_MATERIAL_FILTER}
        )
        WHERE primary_material_id IS NULL
          AND status IN ('discovered', 'matched', 'draft_failed', 'review_required', 'send_failed')
          AND EXISTS (
              SELECT 1
              FROM identity_profiles
              JOIN identity_materials
                ON identity_materials.id = identity_profiles.current_primary_material_id
              WHERE identity_profiles.id = email_tasks.identity_id
                AND identity_materials.identity_id = email_tasks.identity_id
                AND {PRIMARY_MATERIAL_FILTER}
          )
        """
    )


def downgrade() -> None:
    # The migration restores missing task material references from current identity
    # defaults. There is no reliable way to distinguish restored values from
    # user-selected values after the upgrade.
    pass
