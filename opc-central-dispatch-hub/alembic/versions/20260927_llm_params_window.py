"""ADR-056：LLM 调用参数与窗口口径校正（模型名退役替换 + 窗口 64K→1M）。

Revision ID: 20260927_llm_params_window
Revises: 20260927_task_progress_detail
Create Date: 2026-09-27

官方依据（https://api-docs.deepseek.com/quick_start/pricing/）：
- 模型名 ``deepseek-v4-flash`` 已退役，请求由 DeepSeek-V4.1-Flash 承接，推荐名 ``deepseek-flash``；
- 该模型 CONTEXT LENGTH = 1M（旧配置 65536 只用到 6.5%，导致压缩在真实占用 3.7% 时就触发）。

本迁移为**幂等数据修正**（无表结构变更）：
1. 存量把 ``deepseek-v4-flash`` 改名/等价的旧标识统一为 ``deepseek-flash``；
2. 把该模型的 ``context_window`` 从旧的 65536 校正为 1000000。

只改「仍是旧值」的行，避免覆盖用户手工调整过的窗口（ADR-030 允许编辑窗口）。
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260927_llm_params_window"
down_revision = "20260927_task_progress_detail"
branch_labels = None
depends_on = None

#: 退役模型标识 → 推荐模型标识（官方：旧名仍被接受但已退役）
_RETIRED_MODELS = ("deepseek-v4-flash", "deepseek-v4-flash-vision-exp")
_TARGET_MODEL = "deepseek-flash"
#: 旧默认窗口（ADR-041 初始值）→ 官方真实窗口
_OLD_WINDOW = 65536
_NEW_WINDOW = 1000000


def upgrade() -> None:
    """把退役模型名与旧默认窗口校正为官方口径（幂等）。"""
    connection = op.get_bind()
    # 1) 模型名替换（仅替换仍是退役名的行）
    connection.execute(
        sa.text(
            "UPDATE llm_providers SET model = :target "
            "WHERE model = ANY(:retired)"
        ),
        {"target": _TARGET_MODEL, "retired": list(_RETIRED_MODELS)},
    )
    # 2) 窗口校正（仅当仍是旧默认值时；用户已手工改过的不动）
    connection.execute(
        sa.text(
            "UPDATE llm_providers SET context_window = :new_window "
            "WHERE context_window = :old_window"
        ),
        {"new_window": _NEW_WINDOW, "old_window": _OLD_WINDOW},
    )


def downgrade() -> None:
    """回滚窗口为旧默认值；模型名不回退（旧名已退役，回退无意义且会再次失配）。"""
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE llm_providers SET context_window = :old_window "
            "WHERE context_window = :new_window"
        ),
        {"new_window": _NEW_WINDOW, "old_window": _OLD_WINDOW},
    )
