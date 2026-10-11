"""AI 智能调度决策审计数据库模型（ADR-033）。"""

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DispatchDecision(Base, TimestampMixin):
    """调度决策审计表：记录每次 AI（或回退规则）的派单决策，全程可追溯。

    每轮调度中每个任务一条记录：候选集快照、AI 原始输出、校验结果、
    是否回退、最终选择与理由、耗时。
    """

    __tablename__ = "dispatch_decisions"
    __table_args__ = (
        Index("idx_dispatch_decisions_task", "task_id", "created_at"),
        {"comment": "AI 智能调度决策审计（ADR-033）"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 ID",
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", name="fk_dispatch_decisions_tasks_task_id"),
        nullable=False,
        comment="被决策的任务 ID",
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="同轮批量决策标识（同一次 LLM 调用产生的决策共享）",
    )
    candidates: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="候选节点快照 [{id,name,cpu,memory,running,load,agent_type}]",
    )
    ai_raw_output: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="LLM 原始输出文本（回退分析用）",
    )
    ai_choice_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="AI 选择的节点 ID（未采纳或非 AI 决策时为空）",
    )
    validated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="AI 输出是否通过白名单校验",
    )
    fallback: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="是否回退规则分配（幻觉/超时/异常/未开启 AI）",
    )
    final_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="最终分配的节点 ID",
    )
    reason: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="决策理由（AI 给出或回退原因）",
    )
    decision_source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="rule",
        server_default="rule",
        comment="决策来源：ai / rule",
    )
    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="决策耗时（毫秒）",
    )
