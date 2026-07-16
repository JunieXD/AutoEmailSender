"""add endpoint-aware LLM adaptation caches

Revision ID: 20260716_llm_endpoint_adaptation
Revises: 20260709_professor_dashboard_indexes
Create Date: 2026-07-16 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260716_llm_endpoint_adaptation"
down_revision: Union[str, Sequence[str], None] = "20260709_professor_dashboard_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_endpoint_adaptation_cache (
            id INTEGER NOT NULL,
            api_base_url VARCHAR(500) NOT NULL,
            model_name VARCHAR(255) NOT NULL,
            learned_endpoint_kind VARCHAR(32) NOT NULL,
            probed_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_llm_endpoint_adaptation_cache PRIMARY KEY (id),
            CONSTRAINT uq_llm_endpoint_adaptation_cache_target UNIQUE (api_base_url, model_name)
        )
        """,
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_llm_endpoint_adaptation_cache_model_name
        ON llm_endpoint_adaptation_cache (model_name)
        """,
    )

    # Values learned without an endpoint dimension are intentionally regenerated.
    op.execute("DROP INDEX IF EXISTS ix_thinking_adaptation_cache_model_name")
    op.execute("DROP TABLE IF EXISTS thinking_adaptation_cache")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS thinking_adaptation_cache (
            id INTEGER NOT NULL,
            api_base_url VARCHAR(500) NOT NULL,
            model_name VARCHAR(255) NOT NULL,
            endpoint_kind VARCHAR(32) NOT NULL,
            learned_extra_body JSON,
            probed_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_thinking_adaptation_cache PRIMARY KEY (id),
            CONSTRAINT uq_thinking_adaptation_cache_endpoint UNIQUE (api_base_url, model_name, endpoint_kind)
        )
        """,
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_thinking_adaptation_cache_model_name
        ON thinking_adaptation_cache (model_name)
        """,
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_thinking_adaptation_cache_model_name")
    op.execute("DROP TABLE IF EXISTS thinking_adaptation_cache")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS thinking_adaptation_cache (
            id INTEGER NOT NULL,
            api_base_url VARCHAR(500) NOT NULL,
            model_name VARCHAR(255) NOT NULL,
            learned_extra_body JSON,
            probed_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_thinking_adaptation_cache PRIMARY KEY (id),
            CONSTRAINT uq_thinking_adaptation_cache_api_base_url UNIQUE (api_base_url, model_name)
        )
        """,
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_thinking_adaptation_cache_model_name
        ON thinking_adaptation_cache (model_name)
        """,
    )

    op.execute("DROP INDEX IF EXISTS ix_llm_endpoint_adaptation_cache_model_name")
    op.execute("DROP TABLE IF EXISTS llm_endpoint_adaptation_cache")
