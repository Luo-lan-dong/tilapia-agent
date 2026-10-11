"""ADR-063 M1：节点 Agent 会话轨迹镜像表 task_agent_trace。

节点侧把「此刻在做什么、怎么思考的」按视图行上报（reasoning/text/tool/todo/usage/status），
中枢只存**视图行**（不存终端字节，D3），按 ``seq`` 任务内单调排列：

- 幂等：唯一约束 ``(task_id, seq)``——节点重试/重复上报同一批不会产生重复行；
- 查询：索引 ``(task_id, seq)``——按任务拉全链、``after_seq`` 增量拉取；
- 单行截断与单任务行数上限由服务层 ``task_agent_trace_service`` 兜底（DB 只管结构）。

Revision ID: 20261001_agent_trace
Revises: 20260928i_experience_env
Create Date: 2026-10-01
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20261001_agent_trace"
down_revision = "20260928i_experience_env"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_agent_trace",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
        sa.Column("task_id", sa.UUID(), nullable=False, comment="所属任务 ID"),
        sa.Column("node_id", sa.String(64), nullable=True, comment="执行节点 ID（来源标注）"),
        sa.Column("session_id", sa.String(64), nullable=True, comment="节点侧会话 ID"),
        sa.Column(
            "seq",
            sa.Integer(),
            nullable=False,
            comment="任务内单调序号（幂等键的一部分，由节点侧递增）",
        ),
        sa.Column(
            "kind",
            sa.String(16),
            nullable=False,
            comment="视图行类型：status/reasoning/text/tool/todo/usage",
        ),
        sa.Column(
            "payload",
            sa.Text(),
            nullable=False,
            comment="视图行内容（JSON 文本，服务层单行截断至 8 KB）",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="创建时间",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name="fk_task_agent_trace_tasks_task_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_agent_trace"),
        # 幂等键：同一（任务, seq）只允许一行（节点重试/重复上报被忽略）
        sa.UniqueConstraint("task_id", "seq", name="uq_task_agent_trace_task_seq"),
        comment="节点 Agent 会话轨迹镜像行（ADR-063 M1）：只读旁观用，按 seq 有序",
    )
    op.create_index(
        "idx_task_agent_trace_task_seq",
        "task_agent_trace",
        ["task_id", "seq"],
    )


def downgrade() -> None:
    op.drop_index("idx_task_agent_trace_task_seq", table_name="task_agent_trace")
    op.drop_table("task_agent_trace")
