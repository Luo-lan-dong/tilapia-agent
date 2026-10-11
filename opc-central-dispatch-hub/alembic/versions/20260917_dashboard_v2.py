"""集群仪表盘 v2：tasks.priority + status 含 review + llm_usage_records（ADR-038）。

Revision ID: 20260917_dashboard_v2
Revises: 20260915_addr_registry
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260917_dashboard_v2"
down_revision = "20260915_addr_registry"
branch_labels = None
depends_on = None

#: 旧版任务状态 CHECK（ADR-038 前）
_OLD_STATUS_CHECK = "status IN ('pending', 'assigned', 'running', 'completed', 'failed')"
#: 新版任务状态 CHECK（引入 review，ADR-038）
_NEW_STATUS_CHECK = "status IN ('pending', 'assigned', 'running', 'completed', 'failed', 'review')"


def upgrade() -> None:
    # 1. tasks 新增 priority 列（默认 P2）
    op.add_column(
        "tasks",
        sa.Column(
            "priority",
            sa.String(8),
            nullable=False,
            server_default=sa.text("'P2'"),
            comment="任务优先级 P0|P1|P2（ADR-038）；暂无自动定级，默认 P2",
        ),
    )
    # 2. 重建 status CHECK 约束引入 review（按 database-standards §2.4：
    #    命名约定映射 chk_tasks_status，历史名 status 一并兜底删除）
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS chk_tasks_status")
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS status")
    op.create_check_constraint("status", "tasks", _NEW_STATUS_CHECK)
    # 3. token 用量记录表
    op.create_table(
        "llm_usage_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, comment="主键 ID"),
        sa.Column(
            "source",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'hub'"),
            comment="用量来源：hub 中枢 / agent 节点（预留）",
        ),
        sa.Column(
            "scene",
            sa.String(32),
            nullable=False,
            comment="调用场景：planning/orchestrator/spec_chat 等",
        ),
        sa.Column("model", sa.String(200), nullable=False, comment="模型 ID"),
        sa.Column(
            "prompt_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="输入 token 数",
        ),
        sa.Column(
            "completion_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="输出 token 数",
        ),
        sa.Column(
            "total_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="总 token 数",
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
        sa.CheckConstraint("total_tokens >= 0", name="chk_llm_usage_records_total_tokens"),
    )
    op.create_index(
        "idx_llm_usage_records_created_at",
        "llm_usage_records",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_llm_usage_records_created_at", table_name="llm_usage_records")
    op.drop_table("llm_usage_records")
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS chk_tasks_status")
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS status")
    op.create_check_constraint("status", "tasks", _OLD_STATUS_CHECK)
    op.drop_column("tasks", "priority")
