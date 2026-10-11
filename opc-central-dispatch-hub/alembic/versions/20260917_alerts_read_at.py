"""仪表盘告警已读水位线：system_settings 新增 alerts_read_at（ADR-038）。

Revision ID: 20260917_alerts_read_at
Revises: 20260917_dashboard_v2
Create Date: 2026-09-17
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260917_alerts_read_at"
down_revision = "20260917_dashboard_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "alerts_read_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="仪表盘异常告警已读水位线（ADR-038）：created_at 早于等于该时刻的告警不再返回",
        ),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "alerts_read_at")
