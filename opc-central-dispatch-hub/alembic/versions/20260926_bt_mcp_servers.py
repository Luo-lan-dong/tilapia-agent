"""宝塔 MCP 服务器连接配置表（ADR-049）。

Revision ID: 20260926_bt_mcp
Revises: 20260925_meta_rules_v2
Create Date: 2026-09-26
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260926_bt_mcp"
down_revision = "20260925_meta_rules_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bt_mcp_servers",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            comment="主键 ID",
        ),
        sa.Column(
            "name",
            sa.String(length=100),
            nullable=False,
            comment="显示名（唯一，如：生产服务器-101.13）",
        ),
        sa.Column(
            "local_url",
            sa.Text(),
            nullable=False,
            comment="内网 MCP 地址（与服务器同网段时使用）",
        ),
        sa.Column(
            "public_url",
            sa.Text(),
            nullable=True,
            comment="公网 MCP 地址（跨网段时使用，可为空）",
        ),
        sa.Column(
            "address_mode",
            sa.String(length=20),
            nullable=False,
            server_default="local",
            comment="当前使用地址：local/public",
        ),
        sa.Column(
            "api_token",
            sa.Text(),
            nullable=False,
            comment="Bearer Token（敏感，API 响应脱敏）",
        ),
        sa.Column(
            "ca_cert",
            sa.Text(),
            nullable=True,
            comment="自签 CA 证书链 PEM 全文（内存加载校验，敏感，API 响应脱敏）",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
            comment="是否启用（禁用后编排技能不可见）",
        ),
        sa.Column(
            "remark",
            sa.String(length=500),
            nullable=True,
            comment="备注",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="更新时间",
        ),
        sa.UniqueConstraint("name", name="uk_bt_mcp_servers_name"),
        comment="宝塔 MCP 服务器连接配置（ADR-049）：中枢直连的目标服务器凭据",
    )


def downgrade() -> None:
    op.drop_table("bt_mcp_servers")
