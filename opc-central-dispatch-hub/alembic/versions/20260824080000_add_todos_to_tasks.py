"""add todos to tasks

Revision ID: 7a8b9c0d1e2f
down_revision: e349d9bac81b
Create Date: 2026-08-24 08:00:00

为 ``tasks`` 表新增 ``todos`` 字段（JSONB），用于持久化 Agent 实时上报的待办列表。

详见 ADR-012「tilacode 执行层集成」2026-08-24 增补决策。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a8b9c0d1e2f"
down_revision: str | None = "e349d9bac81b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "todos",
            JSONB,
            nullable=True,
            comment="Agent 实时上报的待办列表",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "todos")
