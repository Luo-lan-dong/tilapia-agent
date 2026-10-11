"""ADR-061 M5a（UT-M5-4）：tasks 新增 progress_tool 列（节点上报的当前工具名）。

节点自 ADR-055 起就在 `agent.task_progress` 中上报 `tool`（`AgentTaskProgressPayload.tool`，
schema 上限 64），但中枢处理器从未消费该字段 —— 上报即丢弃，导致"当前正在执行哪条命令/哪个工具"
在中枢侧不可查（M5c 会话视图与"长时无文件写入"告警都缺这一维度）。

Revision ID: 20260928d_task_progress_tool
Revises: 20260928c_llm_usage_kind
Create Date: 2026-09-28
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260928d_task_progress_tool"
down_revision = "20260928c_llm_usage_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "progress_tool",
            sa.String(64),
            nullable=True,
            comment="Agent 实时上报的当前工具名（ADR-061 M5a）；NULL=未上报",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "progress_tool")
