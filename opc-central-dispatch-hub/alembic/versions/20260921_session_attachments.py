"""会话附件表迁移：session_attachments（ADR-039 P1）。

Revision ID: 20260921_session_att
Revises: 20260921_model_vision
Create Date: 2026-09-21
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260921_session_att"
down_revision = "20260921_model_vision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="归属任务构建会话 ID（task_building_sessions.id）",
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="关联任务 ID（创建任务时回填）",
        ),
        sa.Column("file_name", sa.String(255), nullable=False, comment="原始文件名"),
        sa.Column(
            "mime",
            sa.String(100),
            nullable=False,
            comment="MIME 类型（上传时浏览器声明，扩展名兜底推断）",
        ),
        sa.Column("size", sa.BigInteger(), nullable=False, comment="文件字节数"),
        sa.Column(
            "kind",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'image'"),
            comment="附件类别：image | file | archive（ADR-039）",
        ),
        sa.Column(
            "storage_path",
            sa.String(512),
            nullable=False,
            comment="本地落盘相对路径（相对 attachment_storage_dir）",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        comment="会话附件（ADR-039）：多模态图片/文件上传与任务派发",
    )
    op.create_index(
        "ix_session_attachments_session_id", "session_attachments", ["session_id"]
    )
    op.create_index("ix_session_attachments_task_id", "session_attachments", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_session_attachments_task_id", table_name="session_attachments")
    op.drop_index("ix_session_attachments_session_id", table_name="session_attachments")
    op.drop_table("session_attachments")
