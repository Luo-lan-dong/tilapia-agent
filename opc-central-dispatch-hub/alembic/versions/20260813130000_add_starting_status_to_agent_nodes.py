"""add starting status to agent_nodes

Revision ID: a1b2c3d4e5f6
Revises: f8a3c2e91b7d
Create Date: 2026-08-13 13:00:00

在 agent_nodes.status 的 CHECK 约束中新增 ``starting`` 状态，
支持 Agent「先注册再上报状态」的启动时序：节点注册后处于 starting，
tilacode 健康检查通过后切换为 idle，调度器只向 idle 节点派发任务。

.. note::
    修改 CHECK 约束时必须删除旧约束。历史上该约束曾由 SQLAlchemy
    自动生成名为 ``chk_agent_nodes_status``，后改为显式命名 ``status``。
    因此 upgrade 中同时删除这两个名称，避免旧约束残留导致 ``starting``
    值仍被判为非法。详见 ``docs/standards/database-standards.md`` 第 2.4 节。

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f8a3c2e91b7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 删除旧版 SQLAlchemy 自动生成的约束（如果存在），再添加新约束
    op.execute(
        "ALTER TABLE agent_nodes DROP CONSTRAINT IF EXISTS chk_agent_nodes_status"
    )
    op.execute(
        "ALTER TABLE agent_nodes DROP CONSTRAINT IF EXISTS status"
    )
    op.execute(
        "ALTER TABLE agent_nodes ADD CONSTRAINT status "
        "CHECK (status IN ('offline', 'starting', 'idle', 'busy', 'error'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE agent_nodes DROP CONSTRAINT IF EXISTS status"
    )
    op.execute(
        "ALTER TABLE agent_nodes ADD CONSTRAINT status "
        "CHECK (status IN ('offline', 'idle', 'busy', 'error'))"
    )
