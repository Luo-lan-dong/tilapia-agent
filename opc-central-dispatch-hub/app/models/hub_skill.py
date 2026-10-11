"""中枢技能数据库模型（ADR-033）。"""

import uuid

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class HubSkill(Base, TimestampMixin):
    """中枢技能表：AI 对话 Agent 可调用的工具注册与启用状态。

    技能本体由代码注册（app/skills/），本表仅维护元数据展示与启用开关；
    无记录时按代码默认（全部启用）兜底。
    """

    __tablename__ = "hub_skills"
    __table_args__ = (
        {"comment": "中枢技能注册表（ADR-033）：元数据展示与启用开关"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 ID",
    )
    skill_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="技能唯一标识，如 find_idle_nodes",
    )
    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="技能展示名称",
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="技能一句话描述",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="启用开关：禁用后 Orchestrator 工具集过滤该技能",
    )
