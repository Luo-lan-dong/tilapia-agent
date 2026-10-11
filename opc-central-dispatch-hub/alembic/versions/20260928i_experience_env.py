"""ADR-061 M6：经验记忆的环境签名与来源（跨机复用匹配键）。

**Q14 已裁（节点侧计算 + 心跳上报）**：节点把自己的环境（OS/架构/运行时/关键工具版本）
哈希成短签名，经**心跳**上报；中枢存 `agent_nodes.environment_signature`。

本迁移同时给经验条目补两列：
- `source_node_id`：回溯是哪台机器踩的坑；
- `environment_signature`：该经验产生时的环境快照——**跨机召回按它匹配**，
  避免把 Windows 专属解法喂给 Linux 节点（NULL = 环境未知，只能做兜底候选）。

Revision ID: 20260928i_experience_env
Revises: 20260928h_budget_policy
Create Date: 2026-09-29
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260928i_experience_env"
down_revision = "20260928h_budget_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_nodes",
        sa.Column(
            "environment_signature",
            sa.String(64),
            nullable=True,
            comment="环境签名（ADR-061 M6）：节点心跳上报的 OS/架构/运行时/工具版本短哈希",
        ),
    )
    op.add_column(
        "experience_records",
        sa.Column(
            "source_node_id",
            sa.String(64),
            nullable=True,
            comment="来源节点 ID（ADR-061 M6）",
        ),
    )
    op.add_column(
        "experience_records",
        sa.Column(
            "environment_signature",
            sa.String(64),
            nullable=True,
            comment="环境签名快照（ADR-061 M6）：跨机召回匹配键；NULL=环境未知",
        ),
    )
    # 跨机召回按 (环境签名, 状态) 过滤并按命中数排序，加复合索引避免全表扫
    op.create_index(
        "idx_experience_records_env_status",
        "experience_records",
        ["environment_signature", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_experience_records_env_status", table_name="experience_records")
    op.drop_column("experience_records", "environment_signature")
    op.drop_column("experience_records", "source_node_id")
    op.drop_column("agent_nodes", "environment_signature")
