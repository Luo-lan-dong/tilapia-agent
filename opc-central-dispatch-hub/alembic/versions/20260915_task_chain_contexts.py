"""任务链项目上下文记忆表（ADR-034：跨 Agent 任务现场迁移）。

Revision ID: 20260915_chain_context
Revises: 20260914_ai_skills
Create Date: 2026-09-15
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260915_chain_context"
down_revision = "20260914_ai_skills"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_chain_contexts",
        sa.Column("root_task_id", postgresql.UUID(as_uuid=True), nullable=False, comment="任务链根任务 ID（唯一，链内覆盖更新）"),
        sa.Column("context_md", sa.Text(), nullable=False, comment="最新 project_context.md 全文"),
        sa.Column("source_task_id", postgresql.UUID(as_uuid=True), nullable=False, comment="产生该上下文的任务 ID"),
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False, comment="主键 ID"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="更新时间"),
        sa.ForeignKeyConstraint(["root_task_id"], ["tasks.id"], name="fk_task_chain_contexts_tasks_root_task_id"),
        sa.PrimaryKeyConstraint("id", name="pk_task_chain_contexts"),
        sa.UniqueConstraint("root_task_id", name="uk_task_chain_contexts_root_task_id"),
        comment="任务链项目上下文记忆（ADR-034）：链维度最新 project_context.md",
    )
    op.create_index(
        op.f("idx_task_chain_contexts_root_task_id"),
        "task_chain_contexts",
        ["root_task_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("idx_task_chain_contexts_root_task_id"), table_name="task_chain_contexts")
    op.drop_table("task_chain_contexts")
