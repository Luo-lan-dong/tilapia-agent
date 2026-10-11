"""预算闸测试（ADR-061 M5b）：归一化、任务级 override、双维度判定、用量聚合、扫描幂等。

覆盖用户已定参数：**80% 软**（只发收尾指令、不停任务）/ **100% 硬**（强制停止）；
**step + token 双维度任一超限即触发**；全局默认 + 任务级 override（按字段覆盖）。
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskStatus
from app.models.tool_call_log import ToolCallLog, ToolCallLogKind
from app.services import budget_policy_service as budget
from app.services.scheduler import enforce_task_budgets
from app.services.task_service import TaskService


def test_normalize_policy_bounds() -> None:
    """UT-M5-3a：非法/越界输入被夹到边界或回退默认（不会把预算设成 0）。"""
    assert budget.normalize_policy(None) == budget.DEFAULT_BUDGET_POLICY
    assert budget.normalize_policy({"max_steps": 0, "soft_ratio": 2}) == {
        "max_steps": 1,
        "max_tokens": budget.DEFAULT_BUDGET_POLICY["max_tokens"],
        "soft_ratio": 0.99,
    }
    assert budget.normalize_policy({"max_steps": "abc"})["max_steps"] == 60


def test_resolve_budget_task_override_wins_per_field() -> None:
    """UT-M5-3b（Q9）：任务级 override 按字段覆盖全局，未覆盖字段沿用全局。"""
    global_policy = {"max_steps": 40, "max_tokens": 1_000_000, "soft_ratio": 0.8}

    resolved = budget.resolve_budget({"max_steps": 10}, global_policy)
    assert resolved["max_steps"] == 10
    assert resolved["max_tokens"] == 1_000_000

    assert budget.resolve_budget(None, global_policy) == budget.normalize_policy(global_policy)


def test_evaluate_levels_on_both_dimensions() -> None:
    """UT-M5-3c（Q11）：steps 或 tokens 任一达阈值即升级（80% 软 / 100% 硬）。"""
    policy = {"max_steps": 100, "max_tokens": 1000, "soft_ratio": 0.8}

    assert budget.evaluate({"steps": 10, "tokens": 100}, policy)["level"] == "ok"
    # steps 到 80% → 软
    assert budget.evaluate({"steps": 80, "tokens": 10}, policy)["level"] == "soft"
    # tokens 到 80% → 软（另一维度也能触发）
    assert budget.evaluate({"steps": 1, "tokens": 800}, policy)["level"] == "soft"
    # 任一维度到 100% → 硬
    assert budget.evaluate({"steps": 100, "tokens": 0}, policy)["level"] == "hard"
    assert budget.evaluate({"steps": 0, "tokens": 1000}, policy)["level"] == "hard"


def test_needs_action_is_idempotent() -> None:
    """UT-M5-3d：同一阈值只触发一次（预算闸不得每轮重复发指令/重复取消）。"""
    policy = {"max_steps": 10, "max_tokens": 1000, "soft_ratio": 0.8}
    soft_task = Task(id=uuid.uuid4(), title="t", agent_type="programming", status="running")
    soft_state = budget.evaluate({"steps": 8, "tokens": 0}, policy)
    assert budget.needs_action(soft_task, soft_state) == "soft"
    soft_task.budget_state = budget.BUDGET_STATE_SOFT
    assert budget.needs_action(soft_task, soft_state) is None
    soft_task.budget_state = budget.BUDGET_STATE_HARD
    assert budget.needs_action(soft_task, soft_state) is None

    hard_task = Task(id=uuid.uuid4(), title="t", agent_type="programming", status="running")
    hard_state = budget.evaluate({"steps": 11, "tokens": 0}, policy)
    assert budget.needs_action(hard_task, hard_state) == "hard"
    hard_task.budget_state = budget.BUDGET_STATE_HARD
    assert budget.needs_action(hard_task, hard_state) is None


@pytest.fixture
async def budget_task(db_session: AsyncSession) -> Task:
    """落库一个执行中的任务（用例后清理任务与其用量行）。"""
    task = Task(
        id=uuid.uuid4(),
        title="预算闸测试任务",
        agent_type="programming",
        status=TaskStatus.RUNNING.value,
    )
    db_session.add(task)
    await db_session.commit()
    yield task
    await db_session.execute(delete(ToolCallLog).where(ToolCallLog.task_id == str(task.id)))
    await db_session.execute(delete(Task).where(Task.id == task.id))
    await db_session.commit()


async def _record_usage(db_session: AsyncSession, task: Task, count: int, tokens: int) -> None:
    """写入 count 条用量行，每条 input=tokens/count、output=0。

    注意 `result_summary` 是 **Text 列**（内容为 JSON 字符串，与 `tool_call_log_service.record`
    的写入格式一致），不是 JSONB。
    """
    per_row = max(1, tokens // max(1, count)) if tokens else 1
    for _ in range(count):
        db_session.add(
            ToolCallLog(
                kind=ToolCallLogKind.LLM_USAGE.value,
                name="agent_llm_usage",
                task_id=str(task.id),
                result_summary=json.dumps(
                    {"input_tokens": per_row, "cache_read_tokens": 5}, ensure_ascii=False
                ),
            )
        )
    await db_session.commit()


async def test_usage_for_task_aggregates_rows(
    db_session: AsyncSession, budget_task: Task
) -> None:
    """UT-M5-3e：steps=用量行数、tokens=Σ(input+output)；cache_read 单列观测、不计入总量。"""
    await _record_usage(db_session, budget_task, count=3, tokens=300)

    usage = await budget.usage_for_task(db_session, str(budget_task.id))

    assert usage["steps"] == 3
    assert usage["tokens"] == 300
    assert usage["cache_read"] == 15


async def test_budget_sweep_soft_then_hard_and_idempotent(
    db_session: AsyncSession, budget_task: Task
) -> None:
    """UT-M5-3（核心）：85% → 发收尾指令一次（不停任务）；105% → 强制停止；两阈值各自幂等。"""
    # 任务级 override 压小预算，便于构造水位（Q9）
    budget_task.budget = {"max_steps": 10, "max_tokens": 1000, "soft_ratio": 0.8}
    await db_session.commit()
    steer = AsyncMock()
    cancel = AsyncMock(return_value={"task_id": str(budget_task.id), "status": "failed"})

    with (
        patch.object(TaskService, "steer_task", steer),
        patch.object(TaskService, "cancel_task", cancel),
    ):
        # 85%：8/10 轮 → 软阈值
        await _record_usage(db_session, budget_task, count=8, tokens=8)
        counts = await enforce_task_budgets(db_session)
        await db_session.commit()
        await db_session.refresh(budget_task)
        assert counts == {"soft": 1, "hard": 0}
        assert steer.await_count == 1
        # 收尾指令必须带水位与「不要再开新方向」的收敛要求（Q10）
        instruction = steer.await_args.args[2]
        assert "预算已用掉" in instruction and "不要再开新方向" in instruction
        assert cancel.await_count == 0
        assert budget_task.budget_state == budget.BUDGET_STATE_SOFT
        assert budget_task.status == TaskStatus.RUNNING.value, "软阈值不停任务"

        # 重复扫描：同一阈值不得再发
        counts = await enforce_task_budgets(db_session)
        assert counts == {"soft": 0, "hard": 0}
        assert steer.await_count == 1

        # 105%：11/10 轮 → 硬阈值
        await _record_usage(db_session, budget_task, count=3, tokens=3)
        counts = await enforce_task_budgets(db_session)
        await db_session.commit()
        await db_session.refresh(budget_task)
        assert counts == {"soft": 0, "hard": 1}
        assert cancel.await_count == 1
        assert "预算耗尽" in cancel.await_args.kwargs["reason"]
        assert budget_task.budget_state == budget.BUDGET_STATE_HARD

        # 再次扫描：已硬停，不再重复
        counts = await enforce_task_budgets(db_session)
        assert counts == {"soft": 0, "hard": 0}
        assert cancel.await_count == 1


async def test_budget_sweep_soft_failure_retries_next_round(
    db_session: AsyncSession, budget_task: Task
) -> None:
    """UT-M5-3f：收尾指令未送达（节点离线）时**不标记**，下一轮重试（避免"没送达却记成已通知"）。"""
    budget_task.budget = {"max_steps": 10, "max_tokens": 1000, "soft_ratio": 0.8}
    await db_session.commit()
    await _record_usage(db_session, budget_task, count=8, tokens=8)
    steer = AsyncMock(side_effect=RuntimeError("node offline"))

    with patch.object(TaskService, "steer_task", steer):
        counts = await enforce_task_budgets(db_session)

    await db_session.refresh(budget_task)
    assert counts == {"soft": 0, "hard": 0}
    assert budget_task.budget_state is None
