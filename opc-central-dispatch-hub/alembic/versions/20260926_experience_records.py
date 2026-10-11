"""经验记忆条目表（ADR-050）。

Revision ID: 20260926_experience
Revises: 20260926_bt_mcp
Create Date: 2026-09-26
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260926_experience"
down_revision = "20260926_bt_mcp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experience_records",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            comment="主键 ID",
        ),
        sa.Column(
            "tool",
            sa.String(length=64),
            nullable=False,
            server_default="",
            comment="工具名（如 HtmlProjectInfo）；空串表示非工具类经验",
        ),
        sa.Column(
            "error_signature",
            sa.String(length=255),
            nullable=False,
            comment="归一化错误签名（去数字/空白后截断），跨会话检索键",
        ),
        sa.Column(
            "problem",
            sa.Text(),
            nullable=True,
            comment="现象快照（失败自动记录：工具/参数摘要/错误原文）",
        ),
        sa.Column(
            "root_cause",
            sa.Text(),
            nullable=True,
            comment="根因（总结回填，AI 提炼 + 用户确认）",
        ),
        sa.Column(
            "solution",
            sa.Text(),
            nullable=True,
            comment="解法（总结回填，AI 提炼 + 用户确认）",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
            comment="pending（待总结）/ resolved（已总结）",
        ),
        sa.Column(
            "session_id",
            sa.String(length=64),
            nullable=True,
            comment="来源会话 ID",
        ),
        sa.Column(
            "hit_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="命中次数（衡量经验价值）",
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="总结回填时间（status=resolved 时写入）",
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
        comment="经验记忆条目（ADR-050）：失败自动记录 + 收尾总结回填 + 命中注入",
    )
    op.create_index(
        "idx_experience_signature",
        "experience_records",
        ["error_signature"],
    )
    op.create_index("idx_experience_status", "experience_records", ["status"])
    op.create_index("idx_experience_session", "experience_records", ["session_id"])


def downgrade() -> None:
    op.drop_index("idx_experience_session", table_name="experience_records")
    op.drop_index("idx_experience_status", table_name="experience_records")
    op.drop_index("idx_experience_signature", table_name="experience_records")
    op.drop_table("experience_records")
