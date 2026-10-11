"""add execution_mode to tasks

Revision ID: c3d5e7f9a1b2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-14 10:00:00

为 ``tasks`` 表新增 ``execution_mode`` 列，区分任务执行模式：
- ``plan``：仅规划模式，Agent 只生成执行计划不落地代码
- ``build``：构建模式（默认），Agent 按规划执行完整构建

同时添加同名 CHECK 约束限制取值范围，约束命名与既有
``status`` 约束风格一致（直接使用字段名作为约束名）。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d5e7f9a1b2"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "execution_mode",
            sa.String(length=20),
            server_default=sa.text("'build'"),
            nullable=False,
            comment="执行模式 plan|build",
        ),
    )
    op.create_check_constraint(
        "execution_mode",
        "tasks",
        "execution_mode IN ('plan', 'build')",
    )


def downgrade() -> None:
    op.drop_constraint("execution_mode", "tasks", type_="check")
    op.drop_column("tasks", "execution_mode")
