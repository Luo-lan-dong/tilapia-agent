"""任务环境要求清单 tasks.environment_requirements（ADR-048 P2）。

Revision ID: 20260925_task_env_req
Revises: 20260925_rule_docs
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260925_task_env_req"
down_revision = "20260925_rule_docs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "environment_requirements",
            JSONB(),
            nullable=True,
            comment="环境要求清单（ADR-048 P2）：[{name, check_cmd, min_version?, install_hint?}]；NULL=无环境要求",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "environment_requirements")
