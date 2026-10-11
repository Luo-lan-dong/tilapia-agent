"""tasks 增加 executor_model 列（ADR-029 任务执行模型选择与凭据中枢下发）。

Revision ID: 20260910_execmodel
Revises: 20260909_outputdir
Create Date: 2026-09-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "20260910_execmodel"
down_revision = "20260909_outputdir"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "executor_model",
            JSONB,
            nullable=True,
            comment="子 Agent 执行模型选择（ADR-029）：{source: hub|opencode_free, model?}；NULL 视为 hub 默认。凭据不落库",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "executor_model")
