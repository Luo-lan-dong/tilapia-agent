"""元规则模板内容重写（ADR-048 修订）：以中枢 .trae/rules 为模板逐节替换路径。

coding-standards 升级为元规则①（含 {{STANDARDS_TABLE}} 动态清单占位符，
指向 docs/standards/）；decision-documents 升级为元规则②（指向
docs/adr、docs/spec、docs/INDEX.md）。规范正文此后统一落 docs/standards/，
不再落 .tilacode/rules/。

Revision ID: 20260925_meta_rules_v2
Revises: 20260925_task_env_req
Create Date: 2026-09-25
"""

import sqlalchemy as sa

from alembic import op

from app.core.rule_doc_templates import BUILTIN_RULE_DOC_TEMPLATES

# revision identifiers, used by Alembic.
revision = "20260925_meta_rules_v2"
down_revision = "20260925_task_env_req"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """upsert 覆盖内置元规则内容（同名内置行强制更新到新模板）。"""
    for template in BUILTIN_RULE_DOC_TEMPLATES:
        op.execute(
            sa.text(
                """
                INSERT INTO standard_docs (name, category, description, apply_when, content, version, is_builtin)
                VALUES (:name, :category, :description, :apply_when, :content, 1, true)
                ON CONFLICT (name) DO UPDATE SET
                    category = EXCLUDED.category,
                    description = EXCLUDED.description,
                    apply_when = EXCLUDED.apply_when,
                    content = EXCLUDED.content,
                    is_builtin = true
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
    """内容变更不可逆还原（模板升级为单向发布），降级为空操作。"""
