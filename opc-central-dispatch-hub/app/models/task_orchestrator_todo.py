"""中枢编排器待办数据库模型（ADR-061 M5d）。

**与 `tasks.todos`（节点 Agent 上报的待办）物理分离**（D5.5 要求）：
本表是**中枢编排器自己的**待办（任务摘要**下段**数据源），由 `todo_write`
技能维护，前端只读展示（Q5 裁决）。

选**独立表**而非 JSONB 列的理由：需要逐项「开始/完成/修改/删除」的**行级
操作**与稳定 ID（前端勾选展示、后续审计），且不触碰 `TaskResponse.model_validate`
的 3 个校验点（避开异步懒加载问题）。
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

#: 待办状态封闭枚举（与节点上报的 todo 状态口径一致）
ORCHESTRATOR_TODO_STATUSES = ("pending", "in_progress", "completed")
#: 单条待办内容最大长度
ORCHESTRATOR_TODO_CONTENT_MAX = 500
#: 单任务待办条数上限（超出报错，不静默截断）
ORCHESTRATOR_TODO_MAX_ITEMS = 50


class TaskOrchestratorTodo(Base, TimestampMixin):
    """中枢编排器自己的待办项（任务维度、按 seq 排序）。"""

    __tablename__ = "task_orchestrator_todos"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed')",
            name="chk_task_orchestrator_todos_status",
        ),
        CheckConstraint(
            "(task_id IS NOT NULL) OR (session_id IS NOT NULL)",
            name="chk_task_orchestrator_todos_owner",
        ),
        Index("idx_task_orchestrator_todos_task_seq", "task_id", "seq"),
        Index("idx_task_orchestrator_todos_session_seq", "session_id", "seq"),
        {"comment": "中枢编排器自己的待办（ADR-061 M5d）：任务摘要下段数据源"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 ID",
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", name="fk_task_orchestrator_todos_tasks_task_id"),
        nullable=True,
        index=True,
        comment="所属任务 ID（ADR-067：create_task 后由会话待办迁移填充；与 session_id 至少其一非空）",
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "task_building_sessions.id",
            name="fk_task_orchestrator_todos_sessions_session_id",
        ),
        nullable=True,
        index=True,
        comment="所属构建会话 ID（ADR-067 待办先行：任务未创建时挂会话，create_task 后迁移到任务）",
    )
    seq: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="展示顺序（任务内追加递增，从 1 开始）",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=f"待办内容（≤{ORCHESTRATOR_TODO_CONTENT_MAX} 字符）",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        server_default="pending",
        comment="状态：pending/in_progress/completed",
    )
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="orchestrator",
        server_default="orchestrator",
        comment="来源：orchestrator（编排器技能）；预留 user 便于将来扩展（本轮只读，Q5）",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="完成时间（status=completed 时写入，回退为未完成时清空）",
    )
