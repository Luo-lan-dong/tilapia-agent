"""system_settings 增加 agent_resource_manifest_url 列（ADR-022）。

Revision ID: 20260906_resource
Revises: 20260902_version
Create Date: 2026-09-06
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260906_resource"
down_revision = "20260902_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "agent_resource_manifest_url",
            sa.String(length=512),
            nullable=True,
            comment="Agent 资源云端仓库 manifest URL（ADR-022）；空=禁用云端检查",
        ),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "agent_resource_manifest_url")
