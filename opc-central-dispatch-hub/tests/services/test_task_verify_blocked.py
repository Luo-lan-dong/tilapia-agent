"""验收阻断流测试（ADR-061 M4）：阻断处理器 / 24h 兜底 / 用户决策。

覆盖：
- `_handle_task_verify_blocked`：置 `verify_blocked`、落验收报告与阻断时刻、
  在构建会话追加「等你决定」消息、写审计；非执行态忽略；
- `fail_expired_verify_blocked_tasks`：超 24h → `failed`；未超时不动；
- `decide_verify_blocked`：`accept` → completed 并记录；`retry` → 清阻断态、摘要拼入 change_brief 并重新派发；
  非阻断态 → 明确报错。
"""

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_node import AgentNode, NodeStatus
from app.models.task import Task, TaskStatus
from app.models.task_building_session import TaskBuildingSession
from app.services.scheduler import fail_expired_verify_blocked_tasks
from app.services.task_event_handler import _handle_task_verify_blocked
from app.services.task_service import TaskService

_BLOCKED_REPORT: dict[str, Any] = {
    "status": "failed",
    "source": "verify_json",
    "repair_rounds": 5,
    "summary": "❌ [L1/必过] 单元测试：`pytest -q` → failed",
    "must_pass_failures": [{"layer": "L1", "name": "单元测试", "cmd": "pytest -q", "status": "failed"}],
    "checks": [],
}


def _make_task(node_id: uuid.UUID, status: str) -> Task:
    """构造未落库任务实例（mock 查询返回）。"""
    return Task(
        id=uuid.uuid4(),
        title="验收阻断测试任务",
        agent_type="programming",
        status=status,
        progress=100,
        assigned_node_id=node_id,
    )


def _make_node(node_id: uuid.UUID) -> AgentNode:
    """构造忙碌节点实例。"""
    return AgentNode(
        id=node_id,
        name="测试节点",
        agent_type="programming",
        capabilities=["pytest"],
        status=NodeStatus.BUSY.value,
        load=1.0,
        registration_token="test-token",
    )


@contextlib.contextmanager
def _patch_handler_session(
    task: Task, node: AgentNode, building: TaskBuildingSession | None
) -> Iterator[AsyncMock]:
    """替换处理器内部的 AsyncSessionLocal（Task/AgentNode/构建会话三类查询）。"""
    session = AsyncMock()

    async def fake_get(model: Any, _: Any) -> Any:
        if model is Task:
            return task
        if model is AgentNode:
            return node
        if model is TaskBuildingSession:
            return building
        return None

    session.get.side_effect = fake_get
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = session
    factory = MagicMock(return_value=session_ctx)
    with patch("app.services.task_event_handler.AsyncSessionLocal", factory):
        yield session


async def test_verify_blocked_handler_sets_state_and_asks_user() -> None:
    """UT-M4-8：阻断事件 → 置 verify_blocked + 落报告/时刻 + 对话区找用户 + 审计。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value)
    building = TaskBuildingSession(id=uuid.uuid4(), title="构建会话", messages=[], mode="thinking")
    task.session_id = building.id
    record = AsyncMock()

    with (
        patch("app.services.tool_call_log_service.record", record),
        _patch_handler_session(task, _make_node(node_id), building),
    ):
        await _handle_task_verify_blocked(
            str(node_id),
            {"task_id": str(task.id), "reason": "自动验收未通过", "report": _BLOCKED_REPORT},
        )

    assert task.status == TaskStatus.VERIFY_BLOCKED.value
    assert task.verify_report == _BLOCKED_REPORT
    assert task.verify_blocked_at is not None
    assert "等待你的决定" in (task.progress_message or "")
    # 对话区出现「等你决定」消息（Q7：中枢主动找人）
    assert building.messages and building.messages[-1]["role"] == "assistant"
    assert "继续修复" in building.messages[-1]["content"]
    assert "接受现状" in building.messages[-1]["content"]
    assert record.await_args.kwargs["name"] == "task_verify_blocked"
    assert record.await_args.kwargs["result_summary"]["repair_rounds"] == 5


async def test_verify_blocked_handler_ignores_wrong_status() -> None:
    """UT-M4-8b：任务不在执行态（如已 completed）时忽略阻断事件，不覆盖状态。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.COMPLETED.value)

    with _patch_handler_session(task, _make_node(node_id), None):
        await _handle_task_verify_blocked(str(node_id), {"task_id": str(task.id)})

    assert task.status == TaskStatus.COMPLETED.value
    assert task.verify_blocked_at is None


@pytest.fixture
async def blocked_task(db_session: AsyncSession) -> Task:
    """落库一个处于阻断态的任务（用例后清理）。"""
    task = Task(
        id=uuid.uuid4(),
        title="阻断超时测试",
        agent_type="programming",
        status=TaskStatus.VERIFY_BLOCKED.value,
        progress=100,
        verify_report=_BLOCKED_REPORT,
        verify_blocked_at=datetime.now(UTC) - timedelta(hours=25),
    )
    db_session.add(task)
    await db_session.commit()
    yield task
    await db_session.execute(delete(Task).where(Task.id == task.id))
    await db_session.commit()


async def test_expired_verify_blocked_becomes_failed(
    db_session: AsyncSession, blocked_task: Task
) -> None:
    """UT-M4-9：阻断超过 24h 无人决定 → 兜底置 failed 并写明原因。"""
    count = await fail_expired_verify_blocked_tasks(db_session, timeout_seconds=24 * 60 * 60)
    await db_session.commit()
    await db_session.refresh(blocked_task)

    assert count == 1
    assert blocked_task.status == TaskStatus.FAILED.value
    assert "24 小时" in (blocked_task.change_summary or "")


async def test_fresh_verify_blocked_untouched(
    db_session: AsyncSession, blocked_task: Task
) -> None:
    """UT-M4-9b：刚进入阻断态（未超时）的任务不被兜底。"""
    blocked_task.verify_blocked_at = datetime.now(UTC)
    await db_session.commit()

    count = await fail_expired_verify_blocked_tasks(db_session, timeout_seconds=24 * 60 * 60)
    await db_session.commit()
    await db_session.refresh(blocked_task)

    assert count == 0
    assert blocked_task.status == TaskStatus.VERIFY_BLOCKED.value


async def test_decide_accept_marks_completed(
    db_session: AsyncSession, blocked_task: Task
) -> None:
    """UT-M4-10：`accept` → 任务完成并记录「验收未通过但用户接受」。"""
    task = await TaskService().decide_verify_blocked(
        db_session, blocked_task.id, "accept", note="已知风险可接受"
    )

    assert task.status == TaskStatus.COMPLETED.value
    assert task.progress == 100
    assert task.verify_blocked_at is None
    assert "用户选择接受现状" in (task.change_summary or "")
    assert "已知风险可接受" in (task.change_summary or "")


async def test_decide_retry_redispatches_with_failure_summary(
    db_session: AsyncSession, blocked_task: Task
) -> None:
    """UT-M4-10b：`retry` → 清阻断态、失败摘要拼入 change_brief 并重新派发。

    ADR-061 验收 #5 修订后语义：blocked 后节点会话可能已结束，steer 会被
    task_steer_ignored 忽略导致任务假死，故改为重新派发——会话 ID 固定派生自
    任务（ses_task_{id}），重新执行 resume 同一 tilacode 会话，上下文延续。
    """
    service = TaskService()
    dispatch = AsyncMock(return_value={"status": "assigned"})
    with patch.object(TaskService, "dispatch_task", dispatch):
        task = await service.decide_verify_blocked(db_session, blocked_task.id, "retry")

    assert task.verify_blocked_at is None
    brief = task.change_brief or ""
    assert "pytest -q" in brief and "继续修复" in brief
    dispatch.assert_awaited_once()
    assert dispatch.await_args.kwargs.get("target") == "code"
