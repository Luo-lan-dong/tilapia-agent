"""add change_summary to tasks

Revision ID: a9c1e4f7b3d6
Revises: f8a9b0c1d2e3
Create Date: 2026-08-17 10:00:00

为 ``tasks`` 表新增 ``change_summary`` 列（Text，允许为空）：build 模式任务
完成时 Agent 生成的本轮修改说明，随 ``agent.task_completed`` 上报回传，
前端在构建页方案 Tab 与迭代卡片下方展示。

详见 ADR-012「中枢对话式评审界面与 change_summary 回传（2026-08-17 增补决策）」。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9c1e4f7b3d6"
down_revision: str | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "change_summary",
            sa.Text(),
            nullable=True,
            comment="build 模式完成时上报的本轮修改说明",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "change_summary")
