"""tasks 增加 executor_model_actual 列（ADR-029 增强：实际执行模型上报）。

Revision ID: 20260911_execmodelactual
Revises: 20260910_execmodel
Create Date: 2026-09-11
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260911_execmodelactual"
down_revision = "20260910_execmodel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "executor_model_actual",
            sa.String(200),
            nullable=True,
            comment="节点上报的实际执行模型（ADR-029 增强）：如 deepseek/deepseek-chat；降级时带原因说明",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "executor_model_actual")
