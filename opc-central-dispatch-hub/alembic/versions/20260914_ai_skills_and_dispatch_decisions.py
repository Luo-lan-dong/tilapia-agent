"""中枢技能表与调度决策审计表（ADR-033：AI 技能体系与智能调度）。

Revision ID: 20260914_ai_skills
Revises: 20260912_concurrent_slots
Create Date: 2026-09-14
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260914_ai_skills"
down_revision = "20260912_concurrent_slots"
branch_labels = None
depends_on = None

# MVP 技能种子数据（代码注册表 app/skills/registry.py 保持一致）
_SEED_SKILLS = [
    {
        "skill_key": "find_idle_nodes",
        "name": "查找空闲节点",
        "description": "查询当前在线、资源水位与并发槽位均合格的候选节点清单",
        "enabled": True,
    },
    {
        "skill_key": "smart_dispatch",
        "name": "智能派发任务",
        "description": "综合节点负载与任务特征，将任务派发到最合理的节点并给出理由",
        "enabled": True,
    },
    {
        "skill_key": "get_node_metrics",
        "name": "查询节点资源详情",
        "description": "查询节点 CPU/内存/磁盘等实时资源监控数据",
        "enabled": True,
    },
    {
        "skill_key": "get_task_status",
        "name": "查询任务状态",
        "description": "查询任务详情或当前任务队列概况",
        "enabled": True,
    },
    {
        "skill_key": "generate_spec_docs",
        "name": "生成三件套规范文档",
        "description": "根据需求对话生成 spec/tasks/checklist 三件套任务规范文档",
        "enabled": True,
    },
]


def upgrade() -> None:
    op.create_table(
        "hub_skills",
        sa.Column("skill_key", sa.String(length=64), nullable=False, comment="技能唯一标识，如 find_idle_nodes"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="技能展示名称"),
        sa.Column("description", sa.Text(), nullable=False, comment="技能一句话描述"),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False, comment="启用开关：禁用后 Orchestrator 工具集过滤该技能"),
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False, comment="主键 ID"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="更新时间"),
        sa.PrimaryKeyConstraint("id", name="pk_hub_skills"),
        sa.UniqueConstraint("skill_key", name="uk_hub_skills_skill_key"),
        comment="中枢技能注册表（ADR-033）：元数据展示与启用开关",
    )
    op.create_index(op.f("idx_hub_skills_skill_key"), "hub_skills", ["skill_key"], unique=True)

    op.create_table(
        "dispatch_decisions",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False, comment="被决策的任务 ID"),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True, comment="同轮批量决策标识（同一次 LLM 调用产生的决策共享）"),
        sa.Column("candidates", postgresql.JSONB(), nullable=True, comment="候选节点快照 [{id,name,cpu,memory,running,load,agent_type}]"),
        sa.Column("ai_raw_output", sa.Text(), nullable=True, comment="LLM 原始输出文本（回退分析用）"),
        sa.Column("ai_choice_node_id", postgresql.UUID(as_uuid=True), nullable=True, comment="AI 选择的节点 ID（未采纳或非 AI 决策时为空）"),
        sa.Column("validated", sa.Boolean(), server_default="false", nullable=False, comment="AI 输出是否通过白名单校验"),
        sa.Column("fallback", sa.Boolean(), server_default="false", nullable=False, comment="是否回退规则分配（幻觉/超时/异常/未开启 AI）"),
        sa.Column("final_node_id", postgresql.UUID(as_uuid=True), nullable=True, comment="最终分配的节点 ID"),
        sa.Column("reason", sa.String(length=512), nullable=True, comment="决策理由（AI 给出或回退原因）"),
        sa.Column("decision_source", sa.String(length=16), server_default="rule", nullable=False, comment="决策来源：ai / rule"),
        sa.Column("duration_ms", sa.Integer(), nullable=True, comment="决策耗时（毫秒）"),
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False, comment="主键 ID"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="更新时间"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_dispatch_decisions_tasks_task_id"),
        sa.PrimaryKeyConstraint("id", name="pk_dispatch_decisions"),
        comment="AI 智能调度决策审计（ADR-033）",
    )
    op.create_index("idx_dispatch_decisions_task", "dispatch_decisions", ["task_id", "created_at"])

    op.add_column(
        "system_settings",
        sa.Column(
            "ai_dispatch_enabled",
            sa.Boolean(),
            nullable=True,
            comment="AI 智能调度开关（ADR-033）；NULL 时回退环境变量 AI_DISPATCH_ENABLED 初始值",
        ),
    )

    # MVP 技能种子数据
    hub_skills = sa.table(
        "hub_skills",
        sa.column("skill_key"),
        sa.column("name"),
        sa.column("description"),
        sa.column("enabled"),
    )
    op.bulk_insert(hub_skills, _SEED_SKILLS)


def downgrade() -> None:
    op.drop_column("system_settings", "ai_dispatch_enabled")
    op.drop_index("idx_dispatch_decisions_task", table_name="dispatch_decisions")
    op.drop_table("dispatch_decisions")
    op.drop_index(op.f("idx_hub_skills_skill_key"), table_name="hub_skills")
    op.drop_table("hub_skills")
