"""merge structured output and outreach template library heads

Revision ID: 20260730_merge_llm_templates
Revises: 20260730_template_library, 20260730_structured_output
Create Date: 2026-07-30 00:00:01.000000
"""

from __future__ import annotations

from typing import Sequence, Union


revision: str = "20260730_merge_llm_templates"
down_revision: Union[str, Sequence[str], None] = (
    "20260730_template_library",
    "20260730_structured_output",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
