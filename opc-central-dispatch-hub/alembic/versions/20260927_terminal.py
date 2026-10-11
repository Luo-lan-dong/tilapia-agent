"""ADR-053 终端域：system_settings 加 terminal_permission_mode 列 + tool_call_logs.kind 白名单补 terminal_command。

Revision ID: 20260927_terminal
Revises: 20260927_bt_call_kind
Create Date: 2026-09-27
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260927_terminal"
down_revision = "20260927_bt_call_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "terminal_permission_mode",
            sa.String(16),
            nullable=True,
            comment="终端命令执行权限模式（ADR-053）：manual/ai_auto/full；NULL 回退 manual",
        ),
    )
    op.drop_constraint("chk_tool_call_logs_kind", "tool_call_logs", type_="check")
    op.create_check_constraint(
        "chk_tool_call_logs_kind",
        "tool_call_logs",
        "kind IN ('llm_call', 'skill_call', 'skill_result', 'compaction', "
        "'steering', 'dispatch', 'agent_task_event', 'bt_mcp_call', "
        "'terminal_command')",
    )


def downgrade() -> None:
    op.drop_constraint("chk_tool_call_logs_kind", "tool_call_logs", type_="check")
    op.create_check_constraint(
        "chk_tool_call_logs_kind",
        "tool_call_logs",
        "kind IN ('llm_call', 'skill_call', 'skill_result', 'compaction', "
        "'steering', 'dispatch', 'agent_task_event', 'bt_mcp_call')",
    )
    op.drop_column("system_settings", "terminal_permission_mode")
