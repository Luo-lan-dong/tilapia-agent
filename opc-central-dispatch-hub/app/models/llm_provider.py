"""LLM Provider 数据库模型。"""

import uuid

from sqlalchemy import Boolean, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class LLMProvider(Base, TimestampMixin):
    """LLM Provider 配置表。"""

    __tablename__ = "llm_providers"
    __table_args__ = (
        {
            "comment": "大模型 Provider 配置，MVP 阶段仅支持 deepseek",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 ID",
    )
    provider: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
        comment="Provider 类型，MVP 仅 deepseek",
    )
    api_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        default="",
        server_default=text("''"),
        comment="API Key，明文存储",
    )
    base_url: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        default="",
        server_default=text("''"),
        comment="接口基础 URL",
    )
    model: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="",
        server_default=text("''"),
        comment="模型名称",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        index=True,
        comment="是否启用，同一时刻仅一个活跃",
    )
    supports_vision: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="是否支持图片输入（ADR-039）；自动选模与带图任务提示的依据",
    )
    context_window: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=128000,
        server_default=text("128000"),
        comment="模型上下文窗口（token，ADR-041/056）；占用百分比与压缩触发线的分母",
    )
