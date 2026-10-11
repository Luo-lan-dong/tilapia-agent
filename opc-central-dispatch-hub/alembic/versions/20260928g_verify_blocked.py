"""ADR-061 M4：tasks 新增 verify_blocked 状态（验收阻断）+ 验收报告/阻断时刻两列。

**为什么需要新状态**（Q7 用户裁决「新增任务状态 verify_blocked」）：
完成闸发现**必过层红或无法验证**时，任务既不是成功也不是失败——代码写完了，等用户在
任务对话区决定「接受现状 / 继续修复」。复用 `review` 会与 plan 审批流撞车
（`plan_review_service` 对 review 任务做方案批准/驳回），故独立成态。

`verify_blocked_at` 供调度器做 **24h 无回应兜底**（超时置 `failed` 并写明原因）；
`verify_report` 落库分层验收明细（命令/退出码/耗时/输出尾部），供前端与排查使用。

Revision ID: 20260928g_verify_blocked
Revises: 20260928f_orchestrator_todos
Create Date: 2026-09-28
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260928g_verify_blocked"
down_revision = "20260928f_orchestrator_todos"
branch_labels = None
depends_on = None

_OLD = "status IN ('pending', 'assigned', 'running', 'completed', 'failed', 'review', 'requeue_pending')"
_NEW = (
    "status IN ('pending', 'assigned', 'running', 'completed', 'failed', 'review', "
    "'requeue_pending', 'verify_blocked')"
)


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "verify_report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="分层验收报告（ADR-061 M4）；NULL=未跑验收",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "verify_blocked_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="验收阻断时刻（ADR-061 M4）：24h 无回应兜底置 failed",
        ),
    )
    op.drop_constraint("status", "tasks", type_="check")
    op.create_check_constraint("status", "tasks", _NEW)


def downgrade() -> None:
    # 回滚前必须把新状态值归位，否则约束重建会失败（阻断态按失败处理）
    op.execute("UPDATE tasks SET status = 'failed' WHERE status = 'verify_blocked'")
    op.drop_constraint("status", "tasks", type_="check")
    op.create_check_constraint("status", "tasks", _OLD)
    op.drop_column("tasks", "verify_blocked_at")
    op.drop_column("tasks", "verify_report")
