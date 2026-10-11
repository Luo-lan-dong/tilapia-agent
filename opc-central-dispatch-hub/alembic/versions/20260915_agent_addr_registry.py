"""Agent 地址注册表：agent_nodes 新增 reported 地址三列（ADR-035）。

Revision ID: 20260915_addr_registry
Revises: 20260915_chain_context
Create Date: 2026-09-16
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260915_addr_registry"
down_revision = "20260915_chain_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_nodes",
        sa.Column("reported_ip", sa.String(64), nullable=True, comment="Agent 自报地址（安装时探测，ADR-035）"),
    )
    op.add_column(
        "agent_nodes",
        sa.Column("reported_api_port", sa.Integer(), nullable=True, comment="Agent 通知 API 端口（ADR-035）"),
    )
    op.add_column(
        "agent_nodes",
        sa.Column("api_token", sa.String(128), nullable=True, comment="Agent 通知 API 认证 Token（仅首次注册落库，ADR-035）"),
    )


def downgrade() -> None:
    op.drop_column("agent_nodes", "api_token")
    op.drop_column("agent_nodes", "reported_api_port")
    op.drop_column("agent_nodes", "reported_ip")
