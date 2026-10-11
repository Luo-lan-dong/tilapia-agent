"""会话附件数据库模型（ADR-039）。"""

import uuid

from sqlalchemy import BigInteger, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SessionAttachment(Base, TimestampMixin):
    """任务构建会话附件。

    上传时归属对话会话（session_id）；创建任务时回填 task_id 完成关联，
    派发时中枢按 task_id 取附件并签发一次性下载 token 下发 Agent。
    生命周期独立于会话/任务（不加外键，避免级联删除复杂度）。
    """

    __tablename__ = "session_attachments"
    __table_args__ = (
        Index("ix_session_attachments_session_id", "session_id"),
        Index("ix_session_attachments_task_id", "task_id"),
        {"comment": "会话附件（ADR-039）：多模态图片/文件上传与任务派发"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 ID",
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="归属任务构建会话 ID（task_building_sessions.id）",
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="关联任务 ID（创建任务时回填）",
    )
    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="原始文件名",
    )
    mime: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="MIME 类型（上传时浏览器声明，扩展名兜底推断）",
    )
    size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="文件字节数",
    )
    kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'image'"),
        comment="附件类别：image | file | archive（ADR-039）",
    )
    storage_path: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="本地落盘相对路径（相对 attachment_storage_dir）",
    )
