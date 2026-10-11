"""ADR-061 M5a：tool_call_logs.kind 白名单补 llm_usage（节点 LLM 用量/缓存命中率落库）。

M5a「每任务用量落库」落地：节点经 `agent.task_progress` 上报的 `detail.tokens` / `detail.cost`
此前只写日志（`task_progress_cache_metrics`），无法查询与做成本曲线、预算闸统计。
本迁移只放宽 CHECK 约束，不新增列；写入逻辑只在 `task_event_handler` 侧新增，不改旧 kind 语义。

Revision ID: 20260928c_llm_usage_kind
Revises: 20260928b_requeue_pending
Create Date: 2026-09-28
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260928c_llm_usage_kind"
down_revision = "20260928b_requeue_pending"
branch_labels = None
depends_on = None

_OLD = (
    "kind IN ('llm_call', 'skill_call', 'skill_result', 'compaction', "
    "'steering', 'dispatch', 'agent_task_event', 'bt_mcp_call', "
    "'terminal_command')"
)
_NEW = (
    "kind IN ('llm_call', 'skill_call', 'skill_result', 'compaction', "
    "'steering', 'dispatch', 'agent_task_event', 'bt_mcp_call', "
    "'terminal_command', 'llm_usage')"
)


def upgrade() -> None:
    op.drop_constraint("chk_tool_call_logs_kind", "tool_call_logs", type_="check")
    op.create_check_constraint("chk_tool_call_logs_kind", "tool_call_logs", _NEW)


def downgrade() -> None:
    # 回滚前必须把新 kind 归位，否则收紧约束时会残留非法值导致重建失败。
    # 用量行本身是节点上报的派生事件，归入 agent_task_event 保留数据、不删除。
    op.execute("UPDATE tool_call_logs SET kind = 'agent_task_event' WHERE kind = 'llm_usage'")
    op.drop_constraint("chk_tool_call_logs_kind", "tool_call_logs", type_="check")
    op.create_check_constraint("chk_tool_call_logs_kind", "tool_call_logs", _OLD)
