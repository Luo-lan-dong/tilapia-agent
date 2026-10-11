"""执行链路事件表 tool_call_logs（ADR-047）。

Revision ID: 20260924_tool_call_logs
Revises: 20260923_llm_cache
Create Date: 2026-09-24
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260924_tool_call_logs"
down_revision = "20260923_llm_cache"
branch_labels = None
depends_on = None

#: kind 枚举（与模型 ToolCallLogKind 对应）
_KIND_VALUES = (
    "llm_call",
    "skill_call",
    "skill_result",
    "compaction",
    "steering",
    "dispatch",
    "agent_task_event",
)


def upgrade() -> None:
    op.create_table(
        "tool_call_logs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            comment="主键 ID",
        ),
        sa.Column(
            "request_id",
            sa.String(length=64),
            nullable=True,
            comment="请求链路 ID（structlog contextvars 同源）",
        ),
        sa.Column(
            "session_id",
            sa.String(length=64),
            nullable=True,
            comment="对话会话 ID",
        ),
        sa.Column(
            "task_id",
            sa.String(length=64),
            nullable=True,
            comment="关联任务 ID",
        ),
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
            comment="事件类型：llm_call/skill_call/skill_result/compaction/steering/dispatch/agent_task_event",
        ),
        sa.Column(
            "name",
            sa.String(length=128),
            nullable=False,
            comment="主体名（技能名/模型名/事件名）",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="success",
            comment="状态：success/failed/started",
        ),
        sa.Column("args_summary", sa.Text(), nullable=True, comment="参数摘要（截断 2KB）"),
        sa.Column("result_summary", sa.Text(), nullable=True, comment="结果摘要（截断 4KB）"),
        sa.Column("error", sa.Text(), nullable=True, comment="失败原因"),
        sa.Column("duration_ms", sa.Integer(), nullable=True, comment="耗时（毫秒）"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="创建时间",
        ),
        sa.CheckConstraint(
            f"kind IN ({', '.join(repr(v) for v in _KIND_VALUES)})",
            name="chk_tool_call_logs_kind",
        ),
        comment="执行链路事件表（ADR-047）：技能/LLM/压缩/steering/派发/子Agent 任务事件",
    )
    op.create_index("idx_tool_call_logs_request_id", "tool_call_logs", ["request_id"])
    op.create_index("idx_tool_call_logs_session_id", "tool_call_logs", ["session_id"])
    op.create_index("idx_tool_call_logs_task_id", "tool_call_logs", ["task_id"])
    op.create_index("idx_tool_call_logs_kind_created_at", "tool_call_logs", ["kind", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_tool_call_logs_kind_created_at", table_name="tool_call_logs")
    op.drop_index("idx_tool_call_logs_task_id", table_name="tool_call_logs")
    op.drop_index("idx_tool_call_logs_session_id", table_name="tool_call_logs")
    op.drop_index("idx_tool_call_logs_request_id", table_name="tool_call_logs")
    op.drop_table("tool_call_logs")
