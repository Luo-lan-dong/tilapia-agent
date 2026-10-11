"""ADR-059 D5：tasks 状态约束新增 requeue_pending（待确认改派挂起态）。

`scheduling-resilience-d5-ask-branch.md` 落地：AI 智能调度关闭时，节点离线导致
重新排队的任务不得被调度器自动派发，必须停在 `requeue_pending` 等用户确认。

只改 CHECK 约束，不新增列。

Revision ID: 20260928b_requeue_pending
Revises: 20260928_sched_resilience
Create Date: 2026-09-28
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260928b_requeue_pending"
down_revision = "20260928_sched_resilience"
branch_labels = None
depends_on = None

_OLD = "status IN ('pending', 'assigned', 'running', 'completed', 'failed', 'review')"
_NEW = (
    "status IN ('pending', 'assigned', 'running', 'completed', 'failed', "
    "'review', 'requeue_pending')"
)


def upgrade() -> None:
    op.drop_constraint("status", "tasks", type_="check")
    op.create_check_constraint("status", "tasks", _NEW)


def downgrade() -> None:
    # 回滚前必须把新状态值归位，否则约束重建会失败
    op.execute("UPDATE tasks SET status = 'pending' WHERE status = 'requeue_pending'")
    op.drop_constraint("status", "tasks", type_="check")
    op.create_check_constraint("status", "tasks", _OLD)
