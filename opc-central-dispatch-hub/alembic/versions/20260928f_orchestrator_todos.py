"""ADR-061 M5d：新增 task_orchestrator_todos 表（中枢编排器自己的待办）。

D5.5「任务摘要分两段」的下段数据源：上段仍是节点上报的现有 5 块，下段 = 中枢
编排器自己的待办。与 `tasks.todos`（节点上报 JSONB）**物理分离**，互不覆盖。

Revision ID: 20260928f_orchestrator_todos
Revises: 20260928e_permission_policy
Create Date: 2026-09-28
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260928f_orchestrator_todos"
down_revision = "20260928e_permission_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_orchestrator_todos",
        sa.Column("id", sa.UUID(), nullable=False, comment="主键 ID"),
        sa.Column("task_id", sa.UUID(), nullable=False, comment="所属任务 ID"),
        sa.Column("seq", sa.Integer(), nullable=False, comment="展示顺序（任务内递增）"),
        sa.Column("content", sa.Text(), nullable=False, comment="待办内容（≤500 字符）"),
        sa.Column(
            "status",
            sa.String(16),
            server_default="pending",
            nullable=False,
            comment="状态：pending/in_progress/completed",
        ),
        sa.Column(
            "source",
            sa.String(16),
            server_default="orchestrator",
            nullable=False,
            comment="来源：orchestrator（预留 user）",
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="完成时间",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="更新时间",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed')",
            name="chk_task_orchestrator_todos_status",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name="fk_task_orchestrator_todos_tasks_task_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_orchestrator_todos"),
        comment="中枢编排器自己的待办（ADR-061 M5d）：任务摘要下段数据源",
    )
    op.create_index(
        "idx_task_orchestrator_todos_task_seq",
        "task_orchestrator_todos",
        ["task_id", "seq"],
    )
    op.create_index(
        op.f("ix_task_orchestrator_todos_task_id"),
        "task_orchestrator_todos",
        ["task_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_task_orchestrator_todos_task_id"), table_name="task_orchestrator_todos")
    op.drop_index("idx_task_orchestrator_todos_task_seq", table_name="task_orchestrator_todos")
    op.drop_table("task_orchestrator_todos")
