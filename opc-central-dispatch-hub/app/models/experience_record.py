"""经验记忆条目模型（ADR-050）。

跨会话复用的「结论层」知识：工具调用踩坑的根因与解法。
原始证据在 tool_call_logs（ADR-047），本表只存提炼后的结论。
"""

import uuid

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ExperienceRecord(Base, TimestampMixin):
    """经验记忆表。

    生命周期：工具调用失败自动写入 pending（现象快照）→ 会话收尾
    AI 提炼根因/解法、经用户确认后回填为 resolved（双通道之收尾通道；
    用户自然语言触发为另一通道）。后续同签名失败命中注入并累计
    hit_count。经验是增益不是依赖：service 异常时技能调用不受阻。
    """

    __tablename__ = "experience_records"
    __table_args__ = (
        {
            "comment": "经验记忆条目（ADR-050）：失败自动记录 + 收尾总结回填 + 命中注入",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 ID",
    )
    tool: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
        server_default="",
        comment="工具名（如 HtmlProjectInfo）；空串表示非工具类经验",
    )
    error_signature: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="归一化错误签名（去数字/空白后截断），跨会话检索键",
    )
    problem: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="现象快照（失败自动记录：工具/参数摘要/错误原文）",
    )
    root_cause: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="根因（总结回填，AI 提炼 + 用户确认）",
    )
    solution: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="解法（总结回填，AI 提炼 + 用户确认）",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        server_default="pending",
        comment="pending（待总结）/ resolved（已总结）",
    )
    session_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="来源会话 ID",
    )
    source_node_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="来源节点 ID（ADR-061 M6）：便于回溯是哪台机器踩的坑",
    )
    environment_signature: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment=(
            "环境签名快照（ADR-061 M6）：记录该经验产生时的节点环境；跨机召回按它匹配，"
            "NULL=环境未知（只在无法匹配时作为兜底候选）"
        ),
    )
    hit_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="命中次数（衡量经验价值）",
    )
    resolved_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="总结回填时间（status=resolved 时写入）",
    )
