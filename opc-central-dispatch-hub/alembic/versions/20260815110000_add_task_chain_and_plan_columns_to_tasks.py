"""add task chain and plan columns to tasks

Revision ID: f8a9b0c1d2e3
Revises: e6f7a8b9c0d1
Create Date: 2026-08-15 11:00:00

为 ``tasks`` 表新增任务迭代链与 plan 审批流所需的 4 列（均允许为空）：
- ``parent_task_id``：直接父任务 ID，构成任务迭代链
- ``root_task_id``：任务链根任务 ID（冗余存储，派发时作为 base_task_id 下发）
- ``change_brief``：本轮修改说明（增量需求 / 批准方案 / 驳回反馈，由中枢拼装）
- ``plan_content``：plan 模式产出的规划方案全文（Agent 完成时上报回传）

``parent_task_id`` 仅建普通索引 ``idx_tasks_parent_task_id``，不加外键
约束，与既有风格保持一致（自引用链允许父任务先行删除，由应用层保证一致性）。

详见 ADR-012「任务迭代链与 plan 审批流（2026-08-15 增补决策）」。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8a9b0c1d2e3"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "parent_task_id",
            UUID(as_uuid=True),
            nullable=True,
            comment="直接父任务 ID（任务迭代链）",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "root_task_id",
            UUID(as_uuid=True),
            nullable=True,
            comment="任务链根任务 ID（冗余存储）",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "change_brief",
            sa.Text(),
            nullable=True,
            comment="本轮修改说明（中枢拼装）",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "plan_content",
            sa.Text(),
            nullable=True,
            comment="plan 模式方案全文",
        ),
    )
    op.create_index(
        "idx_tasks_parent_task_id",
        "tasks",
        ["parent_task_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_tasks_parent_task_id", table_name="tasks")
    op.drop_column("tasks", "plan_content")
    op.drop_column("tasks", "change_brief")
    op.drop_column("tasks", "root_task_id")
    op.drop_column("tasks", "parent_task_id")
