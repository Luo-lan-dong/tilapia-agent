"""宝塔 MCP 服务器连接配置模型（ADR-049）。"""

import uuid

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BtMcpServer(Base, TimestampMixin):
    """宝塔 MCP 服务器连接表。

    中枢直连宝塔官方 MCP（ADR-049）：每行对应一台目标服务器的
    宝塔 MCP 插件实例。`local_url`/`public_url` 对应插件生成的
    双地址（证书 SAN 只覆盖其一），`address_mode` 记录中枢当前
    实际使用的地址。`api_token`/`ca_cert` 属敏感凭据，API 响应
    一律脱敏；`ca_cert` 为自签 IP 证书场景的 PEM 证书链全文。
    """

    __tablename__ = "bt_mcp_servers"
    __table_args__ = (
        {
            "comment": "宝塔 MCP 服务器连接配置（ADR-049）：中枢直连的目标服务器凭据",
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
        comment="显示名（唯一，如：生产服务器-101.13）",
    )
    local_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="内网 MCP 地址（与服务器同网段时使用）",
    )
    public_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="公网 MCP 地址（跨网段时使用，可为空）",
    )
    address_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="local",
        server_default="local",
        comment="当前使用地址：local/public",
    )
    api_token: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Bearer Token（敏感，API 响应脱敏）",
    )
    ca_cert: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="自签 CA 证书链 PEM 全文（内存加载校验，敏感，API 响应脱敏）",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="是否启用（禁用后编排技能不可见）",
    )
    remark: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="备注",
    )
