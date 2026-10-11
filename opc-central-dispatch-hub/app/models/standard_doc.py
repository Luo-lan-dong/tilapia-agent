"""开发规则文档数据库模型（ADR-048）。"""

import uuid

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class StandardDoc(Base, TimestampMixin):
    """开发规则文档表。

    中枢侧规则文档库：DB 存储即"下发前固化在本地中枢"（ADR-048），
    节点离线改派时直接读库，不重新生成。`apply_when` 记录"何时必须加载"，
    用于 Agent 端生成按需加载索引（AGENTS.md）。
    """

    __tablename__ = "standard_docs"
    __table_args__ = (
        {
            "comment": "开发规则文档库（ADR-048）：预置模板与用户维护的规范文档",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 ID",
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        comment="文档名（唯一，节点端落盘文件名由它净化生成）",
    )
    category: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="分类：coding-standard/decision/custom",
    )
    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="用途说明（AI 编排介绍时使用）",
    )
    apply_when: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="何时必须加载（节点端 AGENTS.md 索引用）",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Markdown 全文",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        comment="版本号，每次更新 +1",
    )
    is_builtin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="是否系统预置模板",
    )
