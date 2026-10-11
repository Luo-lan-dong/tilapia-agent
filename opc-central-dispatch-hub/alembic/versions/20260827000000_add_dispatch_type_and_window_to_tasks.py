"""add_dispatch_type_and_window_to_tasks

按 ADR-017 在 tasks 表新增 dispatch_type 与 dispatch_window_until 字段，
并创建复合索引 (dispatch_type, dispatch_window_until) 加速调度器扫描。

Revision ID: 20260827_task_dispatch
Revises: 20260826_node
Create Date: 2026-08-27 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260827_task_dispatch"
down_revision: str | None = "20260826_node"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """新增 dispatch_type / dispatch_window_until 字段 + 复合索引。"""
    # dispatch_type：CHECK 约束 auto/manual，默认 auto，NOT NULL
    op.add_column(
        "tasks",
        sa.Column(
            "dispatch_type",
            sa.String(16),
            nullable=False,
            server_default="auto",
        ),
    )
    op.create_check_constraint(
        "dispatch_type",
        "tasks",
        "dispatch_type IN ('auto', 'manual')",
    )

    # dispatch_window_until：可空 TIMESTAMPTZ，任务创建时由 Service 注入
    op.add_column(
        "tasks",
        sa.Column(
            "dispatch_window_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # 复合索引：调度器扫描 WHERE dispatch_type='auto' AND window_until <= now()
    op.create_index(
        "idx_tasks_dispatch_type",
        "tasks",
        ["dispatch_type", "dispatch_window_until"],
    )


def downgrade() -> None:
    """回滚：删除索引 + 两个字段。"""
    op.drop_index("idx_tasks_dispatch_type", table_name="tasks")
    op.drop_column("tasks", "dispatch_window_until")
    op.drop_column("tasks", "dispatch_type")
