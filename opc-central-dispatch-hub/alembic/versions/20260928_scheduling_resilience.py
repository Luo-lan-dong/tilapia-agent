"""ADR-059 调度准入与产物韧性：tasks 表增指定节点 / 拒单原因 / 排队原因 / 强行派送标记。

Revision ID: 20260928_sched_resilience
Revises: 20260927_llm_params_window
Create Date: 2026-09-28
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260928_sched_resilience"
down_revision = "20260927_llm_params_window"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "target_node_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="用户/AI 指定的目标节点 ID（ADR-059 排队等待对象）",
        ),
    )
    op.create_foreign_key(
        "fk_tasks_agent_nodes_target_node_id",
        "tasks",
        "agent_nodes",
        ["target_node_id"],
        ["id"],
    )
    op.create_index("ix_tasks_target_node_id", "tasks", ["target_node_id"])

    op.add_column(
        "tasks",
        sa.Column(
            "last_reject_reason",
            sa.String(64),
            nullable=True,
            comment="最近一次节点拒单原因（ADR-059）："
            "resource_busy/slots_full/bridge_not_ready/...",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "dispatch_hold_reason",
            sa.String(200),
            nullable=True,
            comment="当前排队原因（ADR-059 D3，含缺口数值）；NULL=未在排队",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "forced_dispatch",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="是否用户明确要求越过准入门槛强行派送（ADR-059 D2 例外）",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "forced_dispatch")
    op.drop_column("tasks", "dispatch_hold_reason")
    op.drop_column("tasks", "last_reject_reason")
    op.drop_index("ix_tasks_target_node_id", table_name="tasks")
    op.drop_constraint("fk_tasks_agent_nodes_target_node_id", "tasks", type_="foreignkey")
    op.drop_column("tasks", "target_node_id")
