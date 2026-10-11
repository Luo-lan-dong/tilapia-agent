"""tool_call_logs.kind 白名单补 bt_mcp_call（修复：CheckConstraint 拒绝致宝塔工具审计从未落库）。

Revision ID: 20260927_bt_call_kind
Revises: 20260926_sop
Create Date: 2026-09-27
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260927_bt_call_kind"
down_revision = "20260926_sop"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("chk_tool_call_logs_kind", "tool_call_logs", type_="check")
    op.create_check_constraint(
        "chk_tool_call_logs_kind",
        "tool_call_logs",
        "kind IN ('llm_call', 'skill_call', 'skill_result', 'compaction', "
        "'steering', 'dispatch', 'agent_task_event', 'bt_mcp_call')",
    )


def downgrade() -> None:
    op.drop_constraint("chk_tool_call_logs_kind", "tool_call_logs", type_="check")
    op.create_check_constraint(
        "chk_tool_call_logs_kind",
        "tool_call_logs",
        "kind IN ('llm_call', 'skill_call', 'skill_result', 'compaction', "
        "'steering', 'dispatch', 'agent_task_event')",
    )
