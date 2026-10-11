"""create_system_versions_table

Revision ID: 20260902_version
Revises: 20260827_task_dispatch
Create Date: 2026-09-02 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260902_version"
down_revision: str | None = "20260827_task_dispatch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="主键 ID"),
        sa.Column("version", sa.String(length=32), nullable=False, comment="版本号，如 v0.1.0"),
        sa.Column("title", sa.String(length=255), nullable=False, comment="版本标题"),
        sa.Column("content", sa.Text(), nullable=False, comment="更新内容（纯文本，按行展示）"),
        sa.Column("release_date", sa.Date(), nullable=False, comment="发布日期"),
        sa.Column(
            "is_current",
            sa.Boolean(),
            server_default="false",
            nullable=False,
            comment="是否当前版本，部分唯一索引保证全局最多一条 true",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="更新时间"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_versions")),
        sa.UniqueConstraint("version", name=op.f("uk_system_versions_version")),
        comment="系统版本记录，用于侧边栏版本号展示与更新日志页面",
    )
    op.create_index(
        "uk_system_versions_current",
        "system_versions",
        ["is_current"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )


def downgrade() -> None:
    op.drop_index("uk_system_versions_current", table_name="system_versions")
    op.drop_table("system_versions")
