"""执行链路事件表模型（ADR-047）。"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ToolCallLogKind(enum.StrEnum):
    """执行链路事件类型（tool_call_logs.kind）。"""

    LLM_CALL = "llm_call"
    SKILL_CALL = "skill_call"
    SKILL_RESULT = "skill_result"
    COMPACTION = "compaction"
    STEERING = "steering"
    DISPATCH = "dispatch"
    AGENT_TASK_EVENT = "agent_task_event"
    TERMINAL_COMMAND = "terminal_command"
    #: 节点侧 LLM 用量与缓存命中率（ADR-061 M5a）：来自 agent.task_progress 的 detail.tokens，
    #: 每个统计点一行，用于成本曲线与预算闸（此前只写日志、不落库）
    LLM_USAGE = "llm_usage"


class ToolCallLog(Base):
    """执行链路事件表模型。

    中枢侧工具/执行链路的结构化持久化：技能调用、LLM 调用、上下文压缩、
    steering、派发决策与子 Agent 任务生命周期事件。溯源 = 按 request_id
    或 session_id 拉全链（时间升序）；保留 90 天（调度器清理）。
    """

    __tablename__ = "tool_call_logs"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('llm_call', 'skill_call', 'skill_result', 'compaction', "
            "'steering', 'dispatch', 'agent_task_event', 'bt_mcp_call', "
            "'terminal_command', 'llm_usage')",
            name="chk_tool_call_logs_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 ID",
    )
    request_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="请求链路 ID（structlog contextvars 同源）",
    )
    session_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="对话会话 ID",
    )
    task_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="关联任务 ID",
    )
    kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="事件类型：llm_call/skill_call/skill_result/compaction/steering/dispatch/agent_task_event",
    )
    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="主体名（技能名/模型名/事件名）",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="success",
        server_default="success",
        comment="状态：success/failed/started",
    )
    args_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="参数摘要（截断 2KB）",
    )
    result_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="结果摘要（截断 4KB）",
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="失败原因",
    )
    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="耗时（毫秒）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        # 应用侧默认时间（UTC aware）：同一事务内 server now() 恒为事务开始
        # 时间，毫秒内连续写入会导致排序不稳定
        default=lambda: datetime.now(UTC),
        comment="创建时间",
    )
