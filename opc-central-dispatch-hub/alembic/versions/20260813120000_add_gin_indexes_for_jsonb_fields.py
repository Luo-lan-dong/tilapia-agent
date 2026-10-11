"""add GIN indexes for JSONB fields

Revision ID: f8a3c2e91b7d
Revises: edbf299ec56f
Create Date: 2026-08-13 12:00:00

为 JSONB 字段添加 GIN 索引，提升 jsonb 路径查询性能：
- tasks.specification
- task_building_sessions.messages
- task_building_sessions.specification
- agent_nodes.capabilities

索引命名遵循 database-standards.md：idx_表名_字段名_gin
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8a3c2e91b7d"
down_revision: str | None = "edbf299ec56f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # tasks.specification
    op.execute(
        "CREATE INDEX idx_tasks_specification_gin ON tasks USING GIN (specification)"
    )
    # task_building_sessions.messages
    op.execute(
        "CREATE INDEX idx_task_building_sessions_messages_gin "
        "ON task_building_sessions USING GIN (messages)"
    )
    # task_building_sessions.specification
    op.execute(
        "CREATE INDEX idx_task_building_sessions_specification_gin "
        "ON task_building_sessions USING GIN (specification)"
    )
    # agent_nodes.capabilities
    op.execute(
        "CREATE INDEX idx_agent_nodes_capabilities_gin "
        "ON agent_nodes USING GIN (capabilities)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_nodes_capabilities_gin")
    op.execute("DROP INDEX IF EXISTS idx_task_building_sessions_specification_gin")
    op.execute("DROP INDEX IF EXISTS idx_task_building_sessions_messages_gin")
    op.execute("DROP INDEX IF EXISTS idx_tasks_specification_gin")
