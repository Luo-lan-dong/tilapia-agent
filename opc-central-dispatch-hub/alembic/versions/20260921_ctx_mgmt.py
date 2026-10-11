"""上下文管理 v1：llm_providers.context_window + task_building_sessions.context_compaction（ADR-041）。

Revision ID: 20260921_ctx_mgmt
Revises: 20260921_session_att
Create Date: 2026-09-21
"""

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "20260921_ctx_mgmt"
down_revision = "20260921_session_att"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_providers",
        sa.Column(
            "context_window",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("65536"),
            comment="模型上下文窗口（token，ADR-041）；占用百分比与压缩触发线的分母",
        ),
    )
    op.add_column(
        "task_building_sessions",
        sa.Column(
            "context_compaction",
            JSONB(),
            nullable=True,
            comment="压缩点（ADR-041）：{summary_text, message_count, created_at}；messages 列原文永不修改",
        ),
    )


def downgrade() -> None:
    op.drop_column("task_building_sessions", "context_compaction")
    op.drop_column("llm_providers", "context_window")
