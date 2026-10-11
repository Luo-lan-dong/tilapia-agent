"""任务链项目上下文记忆数据库模型（ADR-034）。"""

import uuid

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TaskChainContext(Base, TimestampMixin):
    """任务链项目上下文记忆：按任务链（root_task_id）维度存储最新态。

    Agent 在任务完成时由 AI 生成 project_context.md（项目目标/目录结构/
    已完成/进行中/如何继续/注意事项）随完成事件上报，中枢按链覆盖更新，
    供现场迁移（handoff）时随派发 payload 下发给接手的新 Agent。
    """

    __tablename__ = "task_chain_contexts"
    __table_args__ = (
        {"comment": "任务链项目上下文记忆（ADR-034）：链维度最新 project_context.md"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 ID",
    )
    root_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", name="fk_task_chain_contexts_tasks_root_task_id"),
        nullable=False,
        unique=True,
        index=True,
        comment="任务链根任务 ID（唯一，链内覆盖更新）",
    )
    context_md: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="最新 project_context.md 全文",
    )
    source_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="产生该上下文的任务 ID",
    )
