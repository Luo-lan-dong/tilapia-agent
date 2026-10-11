"""任务取消测试（ADR-048 实测修复：子节点开发中无法打断）。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import TaskDispatchError
from app.models.agent_node import AgentNode
from app.models.dispatch_decision import DispatchDecision
from app.models.standard_doc import StandardDoc
from app.models.task import Task, TaskStatus
from app.models.task_building_session import TaskBuildingSession
from app.services.task_service import TaskService


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """清理相关表（保留预置模板）。"""
    await db_session.execute(delete(DispatchDecision))
    await db_session.execute(TaskBuildingSession.__table__.update().values(task_id=None))
    await db_session.execute(delete(TaskBuildingSession))
    await db_session.execute(delete(Task))
    await db_session.execute(delete(AgentNode))
    await db_session.execute(delete(StandardDoc).where(StandardDoc.is_builtin.is_(False)))
    yield


def _make_task(status: str, **kwargs) -> Task:
    return Task(
        title="取消测试任务",
        agent_type="code",
        status=status,
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
        **kwargs,
    )


async def test_cancel_pending_task_marks_failed(db_session: AsyncSession) -> None:
    """pending 任务取消：直接置 failed，无节点需要中断。"""
    task = _make_task(TaskStatus.PENDING.value)
    db_session.add(task)
    await db_session.commit()

    result = await TaskService().cancel_task(db_session, task.id)
    assert result["status"] == TaskStatus.FAILED.value
    assert result["node_notified"] is False

    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.FAILED.value
    assert refreshed.change_summary == "用户手动取消"


async def test_cancel_terminal_task_conflict(db_session: AsyncSession) -> None:
    """终态任务取消返回冲突（TaskDispatchError）。"""
    task = _make_task(TaskStatus.COMPLETED.value, progress=100)
    db_session.add(task)
    await db_session.commit()

    with pytest.raises(TaskDispatchError):
        await TaskService().cancel_task(db_session, task.id)


async def test_cancel_running_task_notifies_node(db_session: AsyncSession) -> None:
    """running 任务取消：置 failed 并推送中断指令（连接管理器 mock 验证推送）。"""
    from unittest.mock import AsyncMock, patch

    node = AgentNode(
        name="n1",
        agent_type="code",
        status="busy",
        client_id="c1",
        registration_token=uuid.uuid4().hex,
    )
    db_session.add(node)
    await db_session.commit()
    task = _make_task(
        TaskStatus.RUNNING.value,
        assigned_node_id=node.id,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    with patch(
        "app.services.connection_manager.connection_manager.send_to_node",
        new_callable=AsyncMock,
        return_value=True,
    ) as send_mock:
        result = await TaskService().cancel_task(db_session, task.id)

    assert result["status"] == TaskStatus.FAILED.value
    assert result["node_notified"] is True
    send_mock.assert_awaited_once()
    message = send_mock.await_args.args[1]
    assert message["event"] == "hub.task_cancel"
    assert message["payload"]["task_id"] == str(task.id)


async def test_cancel_missing_task_not_found(db_session: AsyncSession) -> None:
    """取消不存在的任务返回 404（TaskNotFoundError）。"""
    import pytest as _pytest

    from app.core.errors import TaskNotFoundError

    with _pytest.raises(TaskNotFoundError):
        await TaskService().cancel_task(db_session, uuid.uuid4())
