"""tasks 增加 output_dir 列（ADR-028 任务工作目录绑定与产物资源管理器）。

Revision ID: 20260909_outputdir
Revises: 20260906_resource
Create Date: 2026-09-09
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260909_outputdir"
down_revision = "20260906_resource"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "output_dir",
            sa.String(length=512),
            nullable=True,
            comment="任务产物保存路径（Agent 节点本地目录语义，ADR-028）；空则由 Agent 按默认规则分配",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "output_dir")
