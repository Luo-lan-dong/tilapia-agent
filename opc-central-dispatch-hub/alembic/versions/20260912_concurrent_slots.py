"""agent_nodes 增加 max_concurrent_tasks 列（ADR-032：节点并发任务槽位上限）。

Revision ID: 20260912_concurrent_slots
Revises: 20260911_execmodelactual
Create Date: 2026-09-12
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260912_concurrent_slots"
down_revision = "20260911_execmodelactual"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_nodes",
        sa.Column(
            "max_concurrent_tasks",
            sa.Integer(),
            nullable=False,
            server_default="2",
            comment="节点最大并发执行任务数（ADR-032）：调度槽位判定上限",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_nodes", "max_concurrent_tasks")
