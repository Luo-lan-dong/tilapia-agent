"""任务预算闸（ADR-061 M5b）：度量、判定与收尾指令。

**已确认决策（用户 2026-09-29 裁决）**
- **Q9 配置层级**：`system_settings.budget_policy`（全局默认）+ `tasks.budget`（任务级 override，按字段覆盖）；
- **Q10 80% 软阈值**：**只发收尾指令、不停任务**（让子 Agent 收敛并把现场写清楚）；
- **Q11 度量维度**：**step + token 双维度**，任一超限即触发。

**为什么不用 cost**：V2 会话账本里 `cost` 结构性恒为 0（ADR-060 P4 实测），金额维度不可用；
改用 **steps**（`tool_call_logs(kind='llm_usage')` 行数，等价于 LLM 轮次）与 **tokens**
（Σ(input+output)，cache_read 只作观测不计入总量，避免命中缓存反而"更贵"的错觉）。

**幂等**：任务上的 `budget_state`（NULL / `soft_notified` / `hard_stopped`）保证每个阈值**只触发一次**，
否则调度循环每轮都会重复发指令、重复取消。
"""

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_settings import SystemSettings
from app.models.task import Task
from app.models.tool_call_log import ToolCallLog, ToolCallLogKind

logger = logging.getLogger(__name__)

#: 全局默认预算（steps=LLM 轮次上限；tokens 为 input+output 总量上限）
DEFAULT_BUDGET_POLICY: dict[str, Any] = {
    "max_steps": 60,
    "max_tokens": 2_000_000,
    "soft_ratio": 0.8,
}
#: 取值边界（防误配置把预算设成 0 或负数导致任务一启动就被砍）
_MAX_STEPS_RANGE = (1, 10_000)
_MAX_TOKENS_RANGE = (1, 2_000_000_000)
_SOFT_RATIO_RANGE = (0.1, 0.99)

#: 预算闸状态（任务上只记最新态，保证每阈值只触发一次）
BUDGET_STATE_SOFT = "soft_notified"
BUDGET_STATE_HARD = "hard_stopped"

#: 软阈值收尾指令（Q10：只发指令、不停任务）
WRAP_UP_INSTRUCTION = (
    "【中枢预算提醒】本任务的执行预算已用掉约 {ratio:.0%}（{steps}/{max_steps} 轮，"
    "{tokens}/{max_tokens} tokens）。请**尽快收敛**：\n"
    "1. 完成当前正在做的这一件事，不要再开新方向或大范围重构；\n"
    "2. 把已完成/未完成与如何继续写进 project_context.md，并说明本轮修改；\n"
    "3. 跑一次你手上最小可用的自检，确保留下的状态是可继续的。\n"
    "预算耗尽时中枢会强制停止任务，请在此之前完成收尾。"
)


def _clamp_int(value: Any, bounds: tuple[int, int], fallback: int) -> int:
    """把输入夹到边界内；非法值回退默认。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    return max(bounds[0], min(int(value), bounds[1]))


def normalize_policy(raw: Any) -> dict[str, Any]:
    """归一化预算策略（非法/缺失字段回退默认值）。"""
    if not isinstance(raw, dict):
        return dict(DEFAULT_BUDGET_POLICY)
    soft_ratio = raw.get("soft_ratio")
    if isinstance(soft_ratio, bool) or not isinstance(soft_ratio, (int, float)):
        normalized_ratio = DEFAULT_BUDGET_POLICY["soft_ratio"]
    else:
        normalized_ratio = max(_SOFT_RATIO_RANGE[0], min(float(soft_ratio), _SOFT_RATIO_RANGE[1]))
    return {
        "max_steps": _clamp_int(
            raw.get("max_steps"), _MAX_STEPS_RANGE, DEFAULT_BUDGET_POLICY["max_steps"]
        ),
        "max_tokens": _clamp_int(
            raw.get("max_tokens"), _MAX_TOKENS_RANGE, DEFAULT_BUDGET_POLICY["max_tokens"]
        ),
        "soft_ratio": normalized_ratio,
    }


def resolve_budget(task_budget: Any, global_policy: dict[str, Any]) -> dict[str, Any]:
    """合并任务级 override 与全局默认（**按字段覆盖**）。"""
    base = normalize_policy(global_policy)
    if not isinstance(task_budget, dict):
        return base
    return normalize_policy({**base, **task_budget})


async def load_policy(session: AsyncSession) -> dict[str, Any]:
    """读取全局预算策略（未配置 → 默认值）。"""
    result = await session.execute(
        select(SystemSettings).order_by(SystemSettings.created_at.asc()).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return dict(DEFAULT_BUDGET_POLICY)
    return normalize_policy(row.budget_policy)


async def save_policy(session: AsyncSession, raw: Any) -> dict[str, Any]:
    """保存全局预算策略（设置行不存在时创建）。"""
    policy = normalize_policy(raw)
    result = await session.execute(
        select(SystemSettings).order_by(SystemSettings.created_at.asc()).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemSettings()
        session.add(row)
    row.budget_policy = policy
    await session.commit()
    await session.refresh(row)
    logger.info(
        "budget_policy_saved",
        extra={
            "event": "budget_policy_saved",
            "max_steps": policy["max_steps"],
            "max_tokens": policy["max_tokens"],
            "soft_ratio": policy["soft_ratio"],
        },
    )
    return policy


async def usage_for_task(session: AsyncSession, task_id: Any) -> dict[str, int]:
    """聚合任务用量：steps = `llm_usage` 行数；tokens = Σ(input+output)（cache_read 单列观测）。

    行数即 LLM 轮次数（M5a 每个统计点一行）。注意 `result_summary` 是 **Text 列**、
    内容是 `json.dumps` 后的字符串（`tool_call_log_service._truncate`），故此处需反序列化；
    兼容将来改为 JSONB 的情况；结构异常的行按 0 计，不抛错。
    """
    result = await session.execute(
        select(ToolCallLog.result_summary).where(
            ToolCallLog.task_id == task_id,
            ToolCallLog.kind == ToolCallLogKind.LLM_USAGE.value,
        )
    )
    steps = 0
    tokens = 0
    cache_read = 0
    for summary in result.scalars().all():
        steps += 1
        parsed = summary
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except (ValueError, TypeError):
                parsed = None
        if not isinstance(parsed, dict):
            continue
        for key in ("input_tokens", "output_tokens"):
            value = parsed.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                tokens += int(value)
        cached = parsed.get("cache_read_tokens")
        if isinstance(cached, (int, float)) and not isinstance(cached, bool) and cached > 0:
            cache_read += int(cached)
    return {"steps": steps, "tokens": tokens, "cache_read": cache_read}


def evaluate(usage: dict[str, int], budget: dict[str, Any]) -> dict[str, Any]:
    """判定预算水位：``ok`` / ``soft``（≥soft_ratio）/ ``hard``（≥100%，任一维度）。"""
    steps = int(usage.get("steps") or 0)
    tokens = int(usage.get("tokens") or 0)
    max_steps = max(1, int(budget.get("max_steps") or DEFAULT_BUDGET_POLICY["max_steps"]))
    max_tokens = max(1, int(budget.get("max_tokens") or DEFAULT_BUDGET_POLICY["max_tokens"]))
    soft_ratio = float(budget.get("soft_ratio") or DEFAULT_BUDGET_POLICY["soft_ratio"])
    steps_ratio = steps / max_steps
    tokens_ratio = tokens / max_tokens
    ratio = max(steps_ratio, tokens_ratio)
    if ratio >= 1.0:
        level = "hard"
    elif ratio >= soft_ratio:
        level = "soft"
    else:
        level = "ok"
    return {
        "level": level,
        "ratio": round(ratio, 4),
        "steps": steps,
        "tokens": tokens,
        "steps_ratio": round(steps_ratio, 4),
        "tokens_ratio": round(tokens_ratio, 4),
        "max_steps": max_steps,
        "max_tokens": max_tokens,
        "soft_ratio": soft_ratio,
    }


def wrap_up_instruction(state: dict[str, Any]) -> str:
    """按当前水位渲染收尾指令（Q10 软阈值动作）。"""
    return WRAP_UP_INSTRUCTION.format(
        ratio=float(state.get("ratio") or 0.0),
        steps=state.get("steps"),
        max_steps=state.get("max_steps"),
        tokens=state.get("tokens"),
        max_tokens=state.get("max_tokens"),
    )


def budget_exceeded_reason(state: dict[str, Any]) -> str:
    """硬停原因文案（写入 change_summary 供用户看懂为什么被停）。"""
    return (
        f"任务执行预算耗尽（{state.get('steps')}/{state.get('max_steps')} 轮，"
        f"{state.get('tokens')}/{state.get('max_tokens')} tokens），已按预算闸强制停止。"
    )


def needs_action(task: Task, state: dict[str, Any]) -> str | None:
    """判断是否需要动作（幂等：同一阈值只触发一次）。"""
    if state["level"] == "hard":
        return None if task.budget_state == BUDGET_STATE_HARD else "hard"
    if state["level"] == "soft":
        return None if task.budget_state in (BUDGET_STATE_SOFT, BUDGET_STATE_HARD) else "soft"
    return None
