"""SOP 知识条目模型（ADR-051）。

部署/运维 SOP 的动态存储：builtin seed（内置场景要点）+ learned
（对话中跑通流程后经 sop_save 固化，需用户确认）。bt_deploy_guide 按
scene 命中注入并累计 hit_count；「记忆 → SOP」Tab 管理。
"""

import uuid

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SopRecord(Base, TimestampMixin):
    """SOP 知识表。

    与经验条目（experience_records，踩坑结论）互补：SOP 是正向流程
    知识。source=builtin 为迁移内置的场景要点（可编辑），learned 为
    对话固化的项目 SOP；禁用后 bt_deploy_guide 不再命中。
    """

    __tablename__ = "sop_records"
    __table_args__ = (
        {
            "comment": "SOP 知识条目（ADR-051）：builtin 场景要点 + 对话固化的项目 SOP",
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
        comment="SOP 名称（唯一）",
    )
    scene: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="场景标识（如 html/php/python 或自定义项目标识）",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="SOP 正文（Markdown，要点式）",
    )
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="learned",
        server_default="learned",
        comment="来源：builtin（内置 seed）/ learned（对话固化）",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="是否启用（禁用后 bt_deploy_guide 不命中）",
    )
    hit_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="命中次数（衡量 SOP 价值）",
    )
