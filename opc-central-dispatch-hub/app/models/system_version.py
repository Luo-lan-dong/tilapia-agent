"""系统版本数据库模型。"""

import datetime
import uuid

from sqlalchemy import Boolean, Date, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SystemVersion(Base, TimestampMixin):
    """系统版本记录表。

    存储中枢系统的版本历史与更新日志，`is_current` 通过部分唯一索引
    （WHERE is_current = true）保证全局只有一条当前版本记录。
    """

    __tablename__ = "system_versions"
    __table_args__ = (
        {
            "comment": "系统版本记录，用于侧边栏版本号展示与更新日志页面",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 ID",
    )
    version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
        comment="版本号，如 v0.1.0",
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="版本标题",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="更新内容（纯文本，按行展示）",
    )
    release_date: Mapped[datetime.date] = mapped_column(
        Date,
        nullable=False,
        comment="发布日期",
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="是否当前版本，部分唯一索引保证全局最多一条 true",
    )
