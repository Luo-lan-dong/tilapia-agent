"""任务定向发言测试（ADR-061 M5a-steer）。

与 cancel 的关键区别：**steer 不改变任务状态**，只把一段话送进活动会话；
且节点离线时必须明确失败，不能静默丢弃（否则用户以为话发出去了）。
"""

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
        title="定向发言测试任务",
        agent_type="code",
        status=status,
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
        **kwargs,
    )


async def _add_node(session: AsyncSession) -> AgentNode:
    node = AgentNode(
        name="n1",
        agent_type="code",
        status="busy",
        client_id="c1",
        registration_token=uuid.uuid4().hex,
    )
    session.add(node)
    await session.commit()
    return node


async def test_steer_running_task_sends_event_without_changing_status(db_session: AsyncSession) -> None:
    """running 任务发言：推送 hub.task_steer，且**任务状态不变**（与 cancel 的区别）。"""
    from unittest.mock import AsyncMock, patch

    node = await _add_node(db_session)
    task = _make_task(TaskStatus.RUNNING.value, assigned_node_id=node.id, progress=42)
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    content = "请改为先跑测试再改代码"
    with patch(
        "app.services.connection_manager.connection_manager.send_to_node",
        new_callable=AsyncMock,
        return_value=True,
    ) as send_mock:
        result = await TaskService().steer_task(db_session, task.id, content)

    assert result == {"task_id": str(task.id), "delivered": True, "text_length": len(content)}
    send_mock.assert_awaited_once()
    message = send_mock.await_args.args[1]
    assert message["event"] == "hub.task_steer"
    assert message["payload"]["task_id"] == str(task.id)
    assert message["payload"]["text"] == content

    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.RUNNING.value, "steer 不得改变任务状态"
    assert refreshed.progress == 42


async def test_steer_rejects_task_without_active_session(db_session: AsyncSession) -> None:
    """非 assigned/running 的任务没有活动会话，拒绝发言。"""
    task = _make_task(TaskStatus.COMPLETED.value, progress=100)
    db_session.add(task)
    await db_session.commit()

    with pytest.raises(TaskDispatchError):
        await TaskService().steer_task(db_session, task.id, "还在吗")


async def test_steer_rejects_blank_text(db_session: AsyncSession) -> None:
    """空内容拒绝（避免推一条无意义指令过去）。"""
    node = await _add_node(db_session)
    task = _make_task(TaskStatus.RUNNING.value, assigned_node_id=node.id)
    db_session.add(task)
    await db_session.commit()

    with pytest.raises(TaskDispatchError):
        await TaskService().steer_task(db_session, task.id, "   \n  ")


async def test_steer_rejects_unassigned_task(db_session: AsyncSession) -> None:
    """运行中但未分配节点：没有可发言的会话，明确拒绝。"""
    task = _make_task(TaskStatus.RUNNING.value)
    db_session.add(task)
    await db_session.commit()

    with pytest.raises(TaskDispatchError):
        await TaskService().steer_task(db_session, task.id, "在吗")


async def test_steer_fails_loudly_when_node_offline(db_session: AsyncSession) -> None:
    """节点离线：必须明确失败，不得静默丢弃（否则用户以为话已送出）。"""
    from unittest.mock import AsyncMock, patch

    node = await _add_node(db_session)
    task = _make_task(TaskStatus.RUNNING.value, assigned_node_id=node.id)
    db_session.add(task)
    await db_session.commit()

    with (
        patch(
            "app.services.connection_manager.connection_manager.send_to_node",
            new_callable=AsyncMock,
            return_value=False,
        ),
        pytest.raises(TaskDispatchError),
    ):
        await TaskService().steer_task(db_session, task.id, "在吗")


async def test_steer_missing_task_not_found(db_session: AsyncSession) -> None:
    """任务不存在返回 404（TaskNotFoundError）。"""
    from app.core.errors import TaskNotFoundError

    with pytest.raises(TaskNotFoundError):
        await TaskService().steer_task(db_session, uuid.uuid4(), "在吗")


async def test_steer_persists_user_message_to_building_session(
    db_session: AsyncSession,
) -> None:
    """UT-H5（ADR-064 D5）：steer 成功后用户消息落会话消息（刷新不丢）。

    消息结构与续聊用户消息同构：{role, content, mention_node, ts}。
    """
    from unittest.mock import AsyncMock, patch

    node = await _add_node(db_session)
    task = _make_task(TaskStatus.RUNNING.value, assigned_node_id=node.id)
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    building = TaskBuildingSession(
        title="steer 会话",
        messages=[{"role": "assistant", "content": "执行中…"}],
    )
    db_session.add(building)
    await db_session.commit()
    await db_session.refresh(building)
    task.session_id = building.id
    await db_session.commit()
    await db_session.refresh(task)

    content = "请改为先跑测试再改代码"
    with patch(
        "app.services.connection_manager.connection_manager.send_to_node",
        new_callable=AsyncMock,
        return_value=True,
    ):
        await TaskService().steer_task(db_session, task.id, content)

    fresh = await db_session.get(TaskBuildingSession, building.id)
    assert fresh is not None
    assert len(fresh.messages) == 2
    appended = fresh.messages[-1]
    assert appended["role"] == "user"
    assert appended["content"] == content
    assert appended["mention_node"] == "n1"
    assert appended.get("ts")


async def test_steer_failure_does_not_persist_message(db_session: AsyncSession) -> None:
    """UT-H5 守卫：steer 失败（节点离线）时不落用户消息（未送达不留痕）。"""
    from unittest.mock import AsyncMock, patch

    node = await _add_node(db_session)
    task = _make_task(TaskStatus.RUNNING.value, assigned_node_id=node.id)
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    building = TaskBuildingSession(title="steer 失败会话", messages=[])
    db_session.add(building)
    await db_session.commit()
    await db_session.refresh(building)
    task.session_id = building.id
    await db_session.commit()
    await db_session.refresh(task)

    with (
        patch(
            "app.services.connection_manager.connection_manager.send_to_node",
            new_callable=AsyncMock,
            return_value=False,
        ),
        pytest.raises(TaskDispatchError),
    ):
        await TaskService().steer_task(db_session, task.id, "在吗")

    fresh = await db_session.get(TaskBuildingSession, building.id)
    assert fresh is not None
    assert fresh.messages == []
