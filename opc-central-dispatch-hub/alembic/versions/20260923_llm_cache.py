"""LLM 用量记录加 prompt cache 命中观测列（ADR-044）。

Revision ID: 20260923_llm_cache
Revises: 20260921_env_ctx
Create Date: 2026-09-23
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260923_llm_cache"
down_revision = "20260921_env_ctx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_usage_records",
        sa.Column(
            "prompt_cache_hit_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="prompt cache 命中 token 数（ADR-044）",
        ),
    )
    op.add_column(
        "llm_usage_records",
        sa.Column(
            "prompt_cache_miss_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="prompt cache 未命中 token 数（ADR-044）",
        ),
    )


def downgrade() -> None:
    op.drop_column("llm_usage_records", "prompt_cache_miss_tokens")
    op.drop_column("llm_usage_records", "prompt_cache_hit_tokens")
