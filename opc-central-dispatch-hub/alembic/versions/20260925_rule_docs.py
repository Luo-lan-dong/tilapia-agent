"""开发规则文档库 standard_docs 与 tasks.rule_docs（ADR-048）。

Revision ID: 20260925_rule_docs
Revises: 20260924_tool_call_logs
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op
from app.core.rule_doc_templates import BUILTIN_RULE_DOC_TEMPLATES

# revision identifiers, used by Alembic.
revision = "20260925_rule_docs"
down_revision = "20260924_tool_call_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "standard_docs",
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
            comment="文档名（唯一，节点端落盘文件名由它净化生成）",
        ),
        sa.Column(
            "category",
            sa.String(length=50),
            nullable=True,
            comment="分类：coding-standard/decision/custom",
        ),
        sa.Column(
            "description",
            sa.String(length=500),
            nullable=True,
            comment="用途说明（AI 编排介绍时使用）",
        ),
        sa.Column(
            "apply_when",
            sa.String(length=500),
            nullable=True,
            comment="何时必须加载（节点端 AGENTS.md 索引用）",
        ),
        sa.Column("content", sa.Text(), nullable=False, comment="Markdown 全文"),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="版本号，每次更新 +1",
        ),
        sa.Column(
            "is_builtin",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="是否系统预置模板",
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
        sa.UniqueConstraint("name", name="uk_standard_docs_name"),
        comment="开发规则文档库（ADR-048）：预置模板与用户维护的规范文档",
    )
    op.add_column(
        "tasks",
        sa.Column(
            "rule_docs",
            JSONB(),
            nullable=True,
            comment="任务绑定的规则文档（ADR-048）：[{doc_id, version}] 创建时锁定版本；NULL=不下发",
        ),
    )
    _seed_builtin_templates()


def _seed_builtin_templates() -> None:
    """幂等插入预置模板（已存在同名文档时跳过，不覆盖用户修改）。"""
    for template in BUILTIN_RULE_DOC_TEMPLATES:
        op.execute(
            sa.text(
                """
                INSERT INTO standard_docs (name, category, description, apply_when, content, version, is_builtin)
                VALUES (:name, :category, :description, :apply_when, :content, 1, true)
                ON CONFLICT (name) DO NOTHING
                """
            ).bindparams(
                name=template.name,
                category=template.category,
                description=template.description,
                apply_when=template.apply_when,
                content=template.content,
            )
        )


def downgrade() -> None:
    op.drop_column("tasks", "rule_docs")
    op.drop_table("standard_docs")
