"""add_metrics_snapshot_to_agent_nodes

Revision ID: 32fdabb7d31a
Revises: a9c1e4f7b3d6
Create Date: 2026-08-18 00:23:21.645467

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '32fdabb7d31a'
down_revision: str | None = 'a9c1e4f7b3d6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为 agent_nodes 表新增 metrics_snapshot 字段，用于存储节点最新监控快照。"""
    op.add_column(
        'agent_nodes',
        sa.Column(
            'metrics_snapshot',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='最近一次上报的系统资源监控快照',
        ),
    )


def downgrade() -> None:
    """回滚：删除 agent_nodes 表的 metrics_snapshot 字段。"""
    op.drop_column('agent_nodes', 'metrics_snapshot')
