"""Agent 节点数据库模型。"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Float, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class NodeStatus(StrEnum):
    """Agent 节点状态枚举。"""

    OFFLINE = "offline"
    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"


class AgentNode(Base, TimestampMixin):
    """Agent 节点表模型。"""

    __tablename__ = "agent_nodes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('offline', 'starting', 'idle', 'busy', 'error')",
            name="status",
        ),
        Index(
            "idx_agent_nodes_capabilities_gin",
            "capabilities",
            postgresql_using="gin",
        ),
        # PostgreSQL 唯一索引允许多个 NULL，兼容未携带 client_id 的存量行
        Index(
            "ux_agent_nodes_client_id",
            "client_id",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 ID",
    )
    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
        comment="节点名称",
    )
    agent_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
        comment="Agent 类型",
    )
    capabilities: Mapped[list[Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        default=list,
        comment="能力标签",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=NodeStatus.OFFLINE.value,
        server_default=text("'offline'"),
        index=True,
        comment="节点状态",
    )
    load: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default=text("0.0"),
        comment="节点负载",
    )
    registration_token: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="注册令牌",
    )
    client_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="节点客户端唯一标识（hostname-随机串），跨重启稳定",
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="最后心跳时间",
    )
    metrics_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        default=None,
        comment="最近一次上报的系统资源监控快照",
    )
    environment_signature: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment=(
            "环境签名（ADR-061 M6）：节点侧计算的 OS/架构/运行时/关键工具版本短哈希，"
            "经心跳上报；经验记忆按它做跨机复用的匹配键；NULL=旧节点未上报"
        ),
    )
    max_concurrent_tasks: Mapped[int] = mapped_column(
        nullable=False,
        default=2,
        server_default=text("2"),
        comment="节点最大并发执行任务数（ADR-032）：调度槽位判定上限",
    )
    reported_ip: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Agent 自报地址（安装时探测，ADR-035）",
    )
    reported_api_port: Mapped[int | None] = mapped_column(
        nullable=True,
        comment="Agent 通知 API 端口（ADR-035）",
    )
    api_token: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="Agent 通知 API 认证 Token（仅首次注册落库，ADR-035）",
    )
