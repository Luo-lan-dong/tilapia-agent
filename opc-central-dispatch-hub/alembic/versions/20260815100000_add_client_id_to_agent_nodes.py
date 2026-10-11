"""add client_id to agent_nodes

Revision ID: e6f7a8b9c0d1
Revises: c3d5e7f9a1b2
Create Date: 2026-08-15 10:00:00

为 ``agent_nodes`` 表新增 ``client_id`` 列并建立唯一索引，用于识别
「同一台物理机器」：Agent 节点重启后会重新调用注册接口，中枢依据
client_id 幂等更新既有记录，避免节点表堆积大量重复行。

.. note::
    唯一索引 ``ux_agent_nodes_client_id`` 基于 PostgreSQL 语义：NULL
    之间互不相等，因此存量行（client_id 为 NULL）不会触发唯一冲突，
    可在不清理历史数据的情况下直接创建。

    生产库如存在同一机器的重复记录，需在升级前按 ``name`` / 心跳时间
    人工合并，并为保留行回填 client_id 后再让新版本 Agent 上线。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "c3d5e7f9a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_nodes",
        sa.Column(
            "client_id",
            sa.String(length=255),
            nullable=True,
            comment="节点客户端唯一标识（hostname-随机串），跨重启稳定",
        ),
    )
    op.create_index(
        "ux_agent_nodes_client_id",
        "agent_nodes",
        ["client_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_agent_nodes_client_id", table_name="agent_nodes")
    op.drop_column("agent_nodes", "client_id")
