"""节点 Agent 会话轨迹镜像模型（ADR-063 M1）。

中枢只落**视图行**（status/reasoning/text/tool/todo/usage 六类），不落终端字节；
`(task_id, seq)` 是幂等键（节点批量上报可重复投递），单行截断与单任务行数上限
在 `app.services.task_agent_trace_service` 统一兜底。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: 轨迹行类型封闭集合（服务层据此过滤非法 kind）
AGENT_TRACE_KINDS = ("status", "reasoning", "text", "tool", "todo", "usage")
#: 单行 payload 截断上限（字节）：超过则保留头尾并标记 truncated
AGENT_TRACE_PAYLOAD_MAX_BYTES = 8192
#: 单任务行数上限（超出拒绝新增，由服务层告警）
AGENT_TRACE_MAX_ROWS_PER_TASK = 5000


class TaskAgentTrace(Base):
    """节点 Agent 会话轨迹镜像行（任务维度、按 seq 排列）。"""

    __tablename__ = "task_agent_trace"
    __table_args__ = (
        # 幂等键 UniqueConstraint(task_id, seq) 由迁移建立（仓库既有口径：复合唯一约束
        # 只在 alembic 里声明），服务层用 ON CONFLICT (task_id, seq) DO NOTHING 依赖它
        Index("idx_task_agent_trace_task_seq", "task_id", "seq"),
        {"comment": "节点 Agent 会话轨迹镜像行（ADR-063 M1）：只读旁观用，按 seq 有序"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="主键 ID",
    )
    # 注：tasks.id 为 UUID（仓库既有口径），故此处用 UUID 而非文档里的 str——
    # 字符串列无法建 tasks.id 外键（仓库既有同类子表 dispatch_decisions 同样用 UUID）
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", name="fk_task_agent_trace_tasks_task_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属任务 ID",
    )
    node_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="执行节点 ID（来源标注，前端折叠块标题用）",
    )
    session_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="节点侧会话 ID",
    )
    seq: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="任务内单调序号（幂等键的一部分，由节点侧递增）",
    )
    kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="视图行类型：status/reasoning/text/tool/todo/usage",
    )
    payload: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="视图行内容（JSON 文本，服务层单行截断至 8 KB）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        comment="创建时间",
    )
