"""任务终态自动唤醒编排开关列 + tool_call_logs.kind 约束扩展（ADR-066）。

Revision ID: 20261006_orchestrator_auto_wake
Revises: 20261001_agent_trace
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261006_orchestrator_auto_wake"
down_revision = "20261001_agent_trace"
branch_labels = None
depends_on = None

_OLD_KIND_CONSTRAINT = (
    "kind IN ('llm_call', 'skill_call', 'skill_result', 'compaction', "
    "'steering', 'dispatch', 'agent_task_event', 'bt_mcp_call', "
    "'terminal_command', 'llm_usage')"
)
_NEW_KIND_CONSTRAINT = (
    "kind IN ('llm_call', 'skill_call', 'skill_result', 'compaction', "
    "'steering', 'dispatch', 'agent_task_event', 'bt_mcp_call', "
    "'terminal_command', 'llm_usage', 'orchestrator_wake')"
)
#: 历史建表迁移的命名带表名前缀重复（chk_tool_call_logs_chk_tool_call_logs_kind），
#: 以数据库实际名称为准（2026-10-06 实测 pg_constraint）
_KIND_CONSTRAINT_NAME = "chk_tool_call_logs_chk_tool_call_logs_kind"


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "orchestrator_auto_wake_enabled",
            sa.Boolean(),
            nullable=True,
            comment="任务终态自动唤醒编排开关（ADR-066）；NULL 时回退环境变量 ORCHESTRATOR_AUTO_WAKE_ENABLED 初始值",
        ),
    )
    # 唤醒审计复用 tool_call_logs（限流数据源）：kind 枚举扩展。
    # 用显式 SQL 而非 op.create_check_constraint——项目 naming_convention 会
    # 自动叠加表名前缀导致约束名逐次膨胀（2026-10-06 实测三重前缀事故）
    op.execute(f"ALTER TABLE tool_call_logs DROP CONSTRAINT {_KIND_CONSTRAINT_NAME}")
    op.execute(
        f"ALTER TABLE tool_call_logs ADD CONSTRAINT {_KIND_CONSTRAINT_NAME} "
        f"CHECK ({_NEW_KIND_CONSTRAINT})"
    )


def downgrade() -> None:
    # 先清理唤醒审计行，避免恢复旧约束时违反枚举
    op.execute("DELETE FROM tool_call_logs WHERE kind = 'orchestrator_wake'")
    op.execute(f"ALTER TABLE tool_call_logs DROP CONSTRAINT {_KIND_CONSTRAINT_NAME}")
    op.execute(
        f"ALTER TABLE tool_call_logs ADD CONSTRAINT {_KIND_CONSTRAINT_NAME} "
        f"CHECK ({_OLD_KIND_CONSTRAINT})"
    )
    op.drop_column("system_settings", "orchestrator_auto_wake_enabled")
