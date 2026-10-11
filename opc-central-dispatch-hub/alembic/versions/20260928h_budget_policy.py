"""ADR-061 M5b：预算闸配置与状态列（system_settings.budget_policy + tasks.budget/_state）。

Q9 已定「全局默认 + 任务级 override」：全局默认放 `system_settings.budget_policy`（单行 JSONB），
任务级 override 放 `tasks.budget`（按字段覆盖）；`tasks.budget_state` 用于**幂等**——
每个阈值（80% 软 / 100% 硬）只触发一次，否则调度循环每轮都会重复发指令、重复取消。

Revision ID: 20260928h_budget_policy
Revises: 20260928g_verify_blocked
Create Date: 2026-09-29
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260928h_budget_policy"
down_revision = "20260928g_verify_blocked"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "budget_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="任务执行预算（ADR-061 M5b，全局默认）：{max_steps, max_tokens, soft_ratio}",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "budget",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="任务级预算 override（ADR-061 M5b，按字段覆盖全局）",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "budget_state",
            sa.String(16),
            nullable=True,
            comment="预算闸状态（ADR-061 M5b，幂等）：NULL/soft_notified/hard_stopped",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "budget_state")
    op.drop_column("tasks", "budget")
    op.drop_column("system_settings", "budget_policy")
