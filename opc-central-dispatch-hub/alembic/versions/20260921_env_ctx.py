"""环境上下文注入：task_building_sessions.context_environment（ADR-042）。

Revision ID: 20260921_env_ctx
Revises: 20260921_ctx_mgmt
Create Date: 2026-09-21
"""

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "20260921_env_ctx"
down_revision = "20260921_ctx_mgmt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_building_sessions",
        sa.Column(
            "context_environment",
            JSONB(),
            nullable=True,
            comment="环境块快照（ADR-042）：{block, updated_at}；Baseline 不可变 + 变化增量的对比基准",
        ),
    )


def downgrade() -> None:
    op.drop_column("task_building_sessions", "context_environment")
