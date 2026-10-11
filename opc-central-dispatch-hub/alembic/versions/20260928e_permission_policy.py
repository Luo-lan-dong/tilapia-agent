"""ADR-061 M2b：system_settings 新增 permission_policy 列（全局单条权限策略）。

Q1 已定「全局单条策略」：真源 = ``system_settings.permission_policy``，派发时随
``hub.task_dispatch`` 下发（节点以其为权威、本地工作区配置为缓存）。
NULL 表示未配置 → 双方按**最严**（``unattended`` + 空白名单）执行，不放宽任何权限。

Revision ID: 20260928e_permission_policy
Revises: 20260928d_task_progress_tool
Create Date: 2026-09-28
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260928e_permission_policy"
down_revision = "20260928d_task_progress_tool"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "permission_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "子 Agent 权限策略（ADR-061 M2b，全局单条）：{mode, allow:[{action, resource}]}；"
                "NULL 视为最严"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "permission_policy")
