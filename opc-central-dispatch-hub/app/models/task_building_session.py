"""任务构建会话数据库模型。"""

import uuid

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TaskBuildingSession(Base, TimestampMixin):
    """任务构建会话表。

    持久化 Spec Chat 的多轮对话历史和三件套草案，支持历史会话回溯和继续。
    MVP 阶段无用户体系，会话全局可见。
    """

    __tablename__ = "task_building_sessions"
    __table_args__ = (
        Index(
            "idx_task_building_sessions_messages_gin",
            "messages",
            postgresql_using="gin",
        ),
        Index(
            "idx_task_building_sessions_specification_gin",
            "specification",
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 ID",
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="会话标题，取首条用户消息前 50 字",
    )
    messages: Mapped[list[dict] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="对话历史 [{role, content}, ...]",
    )
    specification: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="当前三件套草案 {spec, tasks, checklist}",
    )
    mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="thinking",
        server_default=text("'thinking'"),
        comment="对话模式：fast / thinking",
    )
    model: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="当前使用的模型标识",
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", name="fk_task_building_sessions_tasks_task_id"),
        nullable=True,
        index=True,
        comment="本会话已创建的任务 ID，恢复会话时继续跟踪方案审批",
    )
    context_compaction: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="压缩点（ADR-041）：{summary_text, message_count, created_at}；messages 列原文永不修改",
    )
    context_environment: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="环境块快照（ADR-042）：{block, updated_at}；Baseline 不可变 + 变化增量的对比基准",
    )
