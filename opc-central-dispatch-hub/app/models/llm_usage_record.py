"""LLM token 用量记录数据库模型（ADR-038）。"""

import uuid

from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class LlmUsageRecord(Base, TimestampMixin):
    """token 用量记录表模型。

    每次中枢 LLM 调用成功后落一行（DeepSeekClient 埋点），用于仪表盘
    「Token 消耗」统计；Agent 节点侧执行用量暂无上报协议（后续迭代）。
    """

    __tablename__ = "llm_usage_records"
    __table_args__ = (
        CheckConstraint("total_tokens >= 0", name="chk_llm_usage_records_total_tokens"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 ID",
    )
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="hub",
        server_default="hub",
        comment="用量来源：hub 中枢 / agent 节点（预留）",
    )
    scene: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="调用场景：planning/orchestrator/spec_chat 等",
    )
    model: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="模型 ID",
    )
    prompt_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="输入 token 数",
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="输出 token 数",
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="总 token 数",
    )
    prompt_cache_hit_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="prompt cache 命中 token 数（ADR-044）",
    )
    prompt_cache_miss_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="prompt cache 未命中 token 数（ADR-044）",
    )
