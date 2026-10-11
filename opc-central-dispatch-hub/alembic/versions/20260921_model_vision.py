"""模型视觉能力标记：llm_providers 新增 supports_vision（ADR-039 P0）。

Revision ID: 20260921_model_vision
Revises: 20260917_alerts_read_at
Create Date: 2026-09-21
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260921_model_vision"
down_revision = "20260917_alerts_read_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_providers",
        sa.Column(
            "supports_vision",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="是否支持图片输入（ADR-039）；自动选模与带图任务提示的依据",
        ),
    )


def downgrade() -> None:
    op.drop_column("llm_providers", "supports_vision")
