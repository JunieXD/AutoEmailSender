"""merge email history and intended direction heads

Revision ID: 20260708_merge_email_history_direction_heads
Revises: 20260707_recent_email_history_sync, 20260707_intended_direction
Create Date: 2026-07-08 15:20:00.000000

"""

from typing import Sequence, Union


revision: str = "20260708_merge_email_history_direction_heads"
down_revision: Union[str, Sequence[str], None] = (
    "20260707_recent_email_history_sync",
    "20260707_intended_direction",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
