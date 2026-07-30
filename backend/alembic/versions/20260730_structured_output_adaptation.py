"""add structured output adaptation cache

Revision ID: 20260730_structured_output
Revises: 20260721_identity_comm_groups
Create Date: 2026-07-30 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260730_structured_output"
down_revision: Union[str, Sequence[str], None] = "20260721_identity_comm_groups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_structured_output_adaptation_cache (
            id INTEGER NOT NULL,
            api_base_url VARCHAR(500) NOT NULL,
            model_name VARCHAR(255) NOT NULL,
            endpoint_kind VARCHAR(32) NOT NULL,
            probe_version INTEGER NOT NULL,
            learned_mode VARCHAR(32) NOT NULL,
            probed_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            expires_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_llm_structured_output_adaptation_cache PRIMARY KEY (id),
            CONSTRAINT uq_llm_structured_output_adaptation_target
                UNIQUE (api_base_url, model_name, endpoint_kind, probe_version)
        )
        """,
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_llm_structured_output_adaptation_cache_model_name
        ON llm_structured_output_adaptation_cache (model_name)
        """,
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS ix_llm_structured_output_adaptation_cache_model_name",
    )
    op.execute("DROP TABLE IF EXISTS llm_structured_output_adaptation_cache")
