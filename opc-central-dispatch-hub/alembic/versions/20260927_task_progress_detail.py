"""ADR-055 执行层加固：tasks 表增 progress_stage / progress_message 列（Agent 实时当前动作）。

Revision ID: 20260927_task_progress_detail
Revises: 20260927_terminal
Create Date: 2026-09-27
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260927_task_progress_detail"
down_revision = "20260927_terminal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "progress_stage",
            sa.String(32),
            nullable=True,
            comment="Agent 实时上报的执行阶段（ADR-055）：preparing/planning/coding/editing/"
            "running_command/searching/testing/compacting/summarizing/idle；NULL=未上报",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "progress_message",
            sa.String(500),
            nullable=True,
            comment="Agent 实时上报的当前动作描述（ADR-055，节点侧截断 ≤120 字符）；NULL=未上报",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "progress_message")
    op.drop_column("tasks", "progress_stage")
