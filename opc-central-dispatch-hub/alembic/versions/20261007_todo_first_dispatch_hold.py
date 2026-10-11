"""待办先行（todos 挂会话）与派发确认门禁（tasks.dispatch_hold）（ADR-067）。

Revision ID: 20261007_todo_first_dispatch_hold
Revises: 20261006_orchestrator_auto_wake
Create Date: 2026-10-07
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261007_todo_dispatch_hold"
down_revision = "20261006_orchestrator_auto_wake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 中枢待办：支持挂会话（待办先行，ADR-067）
    op.add_column(
        "task_orchestrator_todos",
        sa.Column(
            "session_id",
            sa.UUID(),
            nullable=True,
            comment="所属构建会话 ID（ADR-067 待办先行：任务未创建时挂会话，create_task 后迁移到任务）",
        ),
    )
    op.execute(
        "ALTER TABLE task_orchestrator_todos "
        "ADD CONSTRAINT fk_task_orchestrator_todos_sessions_session_id "
        "FOREIGN KEY (session_id) REFERENCES task_building_sessions(id)"
    )
    op.alter_column(
        "task_orchestrator_todos", "task_id", existing_type=sa.UUID(), nullable=True
    )
    op.create_index(
        "idx_task_orchestrator_todos_session_seq",
        "task_orchestrator_todos",
        ["session_id", "seq"],
    )
    op.create_check_constraint(
        "chk_task_orchestrator_todos_owner",
        "task_orchestrator_todos",
        "(task_id IS NOT NULL) OR (session_id IS NOT NULL)",
    )

    # 2) 任务派发确认门禁（ADR-067）：编排创建的任务默认挂起，调度器跳过
    op.add_column(
        "tasks",
        sa.Column(
            "dispatch_hold",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="派发确认门禁（ADR-067）：True=等待用户确认派发，调度器跳过；dispatch_task 执行时清除",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "dispatch_hold")
    op.drop_constraint(
        "chk_task_orchestrator_todos_owner", "task_orchestrator_todos", type_="check"
    )
    op.drop_index(
        "idx_task_orchestrator_todos_session_seq", table_name="task_orchestrator_todos"
    )
    op.drop_constraint(
        "fk_task_orchestrator_todos_sessions_session_id",
        "task_orchestrator_todos",
        type_="foreignkey",
    )
    op.drop_column("task_orchestrator_todos", "session_id")
    op.alter_column(
        "task_orchestrator_todos", "task_id", existing_type=sa.UUID(), nullable=False
    )
