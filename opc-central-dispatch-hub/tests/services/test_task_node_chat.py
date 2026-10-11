"""`@空闲节点` 续聊服务测试（ADR-064 M1，替代旧建任务路径）。

分支语义：running/assigned → TaskDispatchError（应走 steer 补充发言）；
目标节点不存在/离线 → BusinessError 422；任务不存在 → TaskNotFoundError；
成功时**不建任务**，先落用户消息再经 hub.node_chat 下发——payload 带
trace_task_id（任务链最后任务）与 last_seq（该任务轨迹 max(seq)，无则 0）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, TaskDispatchError, TaskNotFoundError
from app.models.agent_node import AgentNode
from app.models.task import Task, TaskStatus
from app.models.task_agent_trace import TaskAgentTrace
from app.models.task_building_session import TaskBuildingSession
from app.services.task_service import TaskService


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """清理相关表（轨迹表先于 tasks 删除，双向外键先解绑再删）。"""
    await db_session.execute(delete(TaskAgentTrace))
    await db_session.execute(update(TaskBuildingSession).values(task_id=None))
    await db_session.execute(update(Task).values(session_id=None))
    await db_session.execute(delete(Task))
    await db_session.execute(delete(TaskBuildingSession))
    await db_session.execute(delete(AgentNode))
    yield


def _make_task(status: str, **kwargs) -> Task:
    return Task(
        title="节点续聊测试任务",
        agent_type="code",
        status=status,
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
        output_dir="projects/20261005-x",
        executor_model={"source": "hub", "model": "deepseek-chat"},
        **kwargs,
    )


async def _add_node(session: AsyncSession, status: str = "idle") -> AgentNode:
    node = AgentNode(
        name="续聊节点",
        agent_type="code",
        status=status,
        client_id="c1",
        registration_token=uuid.uuid4().hex,
    )
    session.add(node)
    await session.commit()
    return node


async def _add_building_session(
    session: AsyncSession,
    task: Task,
    messages: list[dict] | None = None,
) -> TaskBuildingSession:
    building = TaskBuildingSession(title="续聊会话", messages=messages or [])
    session.add(building)
    await session.commit()
    await session.refresh(building)
    task.session_id = building.id
    await session.commit()
    await session.refresh(task)
    return building


async def _add_trace_rows(
    session: AsyncSession,
    task_id: uuid.UUID,
    seqs: list[int],
) -> None:
    for seq in seqs:
        session.add(
            TaskAgentTrace(
                task_id=task_id,
                node_id="n1",
                seq=seq,
                kind="text",
                payload='{"text":"hi"}',
            )
        )
    await session.commit()


async def _capture_send() -> tuple[AsyncMock, list[dict]]:
    """mock WS 下发并收集消息，便于断言 payload。"""
    sends: list[dict] = []

    async def _fake_send(_node_id: str, message: dict) -> bool:
        sends.append(message)
        return True

    mock = AsyncMock(side_effect=_fake_send)
    return mock, sends


async def test_node_chat_sends_hub_node_chat_event_and_returns_reply_to(
    db_session: AsyncSession,
) -> None:
    """空闲 + 在线节点：下发 hub.node_chat（payload 全契约字段）并返回 reply_to。"""
    node = await _add_node(db_session)
    task = _make_task(TaskStatus.COMPLETED.value, progress=100)
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    send_mock, sends = await _capture_send()
    with patch(
        "app.services.connection_manager.connection_manager.send_to_node",
        send_mock,
    ):
        result = await TaskService().node_chat(db_session, task.id, node.id, "把登录按钮改成绿色")

    assert set(result.keys()) == {"reply_to"}
    send_mock.assert_awaited_once()
    message = sends[0]
    assert message["event"] == "hub.node_chat"
    payload = message["payload"]
    assert set(payload.keys()) == {
        "task_id",
        "root_task_id",
        "node_id",
        "text",
        "reply_to",
        "trace_task_id",
        "last_seq",
    }
    assert payload["task_id"] == str(task.id)
    assert payload["root_task_id"] == str(task.id)  # 单任务链根=自身
    assert payload["node_id"] == str(node.id)
    assert payload["text"] == "把登录按钮改成绿色"
    assert payload["reply_to"] == result["reply_to"]
    assert payload["trace_task_id"] == str(task.id)
    assert payload["last_seq"] == 0


async def test_node_chat_computes_trace_task_and_last_seq_on_chain(
    db_session: AsyncSession,
) -> None:
    """链上多任务：trace_task_id=链最后任务，last_seq=该任务轨迹 max(seq)。"""
    node = await _add_node(db_session)
    # 显式错开 created_at：db_session 的 savepoint 模式下 PG now() 恒为外层
    # 事务开始时刻，连续创建的链任务时间戳相同，created_at 排序会退化到 id
    base = datetime.now(UTC)
    root = _make_task(
        TaskStatus.COMPLETED.value,
        progress=100,
        created_at=base,
    )
    db_session.add(root)
    await db_session.commit()
    await db_session.refresh(root)
    child = _make_task(
        TaskStatus.FAILED.value,
        parent_task_id=root.id,
        root_task_id=root.id,
        created_at=base + timedelta(seconds=10),
    )
    db_session.add(child)
    await db_session.commit()
    await db_session.refresh(child)
    # 根任务轨迹 seq 到 3；链最后任务 child 轨迹到 9
    await _add_trace_rows(db_session, root.id, [1, 2, 3])
    await _add_trace_rows(db_session, child.id, [4, 9])

    _, sends = await _capture_send()
    with patch(
        "app.services.connection_manager.connection_manager.send_to_node",
        AsyncMock(side_effect=lambda _nid, msg: sends.append(msg) or True),
    ):
        await TaskService().node_chat(db_session, root.id, node.id, "继续修复")

    payload = sends[0]["payload"]
    assert payload["task_id"] == str(child.id), "trace_task_id 应为链最后任务"
    assert payload["trace_task_id"] == str(child.id)
    assert payload["root_task_id"] == str(root.id)
    assert payload["last_seq"] == 9, "last_seq 应为链最后任务的 max(seq)"


async def test_node_chat_last_seq_zero_when_chain_tail_has_no_trace(
    db_session: AsyncSession,
) -> None:
    """链最后任务无轨迹 → last_seq=0（UT-H2 无轨迹分支）。"""
    node = await _add_node(db_session)
    # 同上：显式错开 created_at，规避 savepoint 模式下时间戳相同导致排序退化
    base = datetime.now(UTC)
    root = _make_task(TaskStatus.COMPLETED.value, progress=100, created_at=base)
    db_session.add(root)
    await db_session.commit()
    await db_session.refresh(root)
    child = _make_task(
        TaskStatus.FAILED.value,
        root_task_id=root.id,
        parent_task_id=root.id,
        created_at=base + timedelta(seconds=10),
    )
    db_session.add(child)
    await db_session.commit()
    await db_session.refresh(child)
    await _add_trace_rows(db_session, root.id, [1, 2])

    _, sends = await _capture_send()
    with patch(
        "app.services.connection_manager.connection_manager.send_to_node",
        AsyncMock(side_effect=lambda _nid, msg: sends.append(msg) or True),
    ):
        await TaskService().node_chat(db_session, root.id, node.id, "继续")

    payload = sends[0]["payload"]
    assert payload["task_id"] == str(child.id)
    assert payload["last_seq"] == 0, "链尾任务无轨迹时 last_seq 应为 0"


async def test_node_chat_persists_user_message(db_session: AsyncSession) -> None:
    """UT-H3：用户 @消息落任务所属会话（role/content/mention_node/ts）。"""
    node = await _add_node(db_session)
    task = _make_task(TaskStatus.COMPLETED.value, progress=100)
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    building = await _add_building_session(
        db_session, task, [{"role": "user", "content": "历史消息"}]
    )

    with patch(
        "app.services.connection_manager.connection_manager.send_to_node",
        AsyncMock(return_value=True),
    ):
        await TaskService().node_chat(db_session, task.id, node.id, "把标题改成深色")

    fresh = await db_session.get(TaskBuildingSession, building.id)
    assert fresh is not None
    assert len(fresh.messages) == 2
    appended = fresh.messages[-1]
    assert appended["role"] == "user"
    assert appended["content"] == "把标题改成深色"
    assert appended["mention_node"] == "续聊节点"
    assert appended.get("ts")


async def test_node_chat_send_failure_keeps_message(db_session: AsyncSession) -> None:
    """WS 下发失败（节点连接已断）：422 明确失败，但用户消息已落库不丢失。"""
    node = await _add_node(db_session)
    task = _make_task(TaskStatus.COMPLETED.value, progress=100)
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    building = await _add_building_session(db_session, task)

    with (
        patch(
            "app.services.connection_manager.connection_manager.send_to_node",
            AsyncMock(return_value=False),
        ),
        pytest.raises(BusinessError) as exc_info,
    ):
        await TaskService().node_chat(db_session, task.id, node.id, "在吗")

    assert exc_info.value.status_code == 422
    fresh = await db_session.get(TaskBuildingSession, building.id)
    assert fresh is not None
    assert fresh.messages and fresh.messages[-1]["content"] == "在吗"


async def test_node_chat_running_task_rejected(db_session: AsyncSession) -> None:
    """running 任务拒绝续聊：应走 steer（前端分流），且不建任务、不下发。"""
    node = await _add_node(db_session)
    task = _make_task(TaskStatus.RUNNING.value, assigned_node_id=node.id)
    db_session.add(task)
    await db_session.commit()

    send_mock, _ = await _capture_send()
    with patch(
        "app.services.connection_manager.connection_manager.send_to_node",
        send_mock,
    ), pytest.raises(TaskDispatchError) as exc_info:
        await TaskService().node_chat(db_session, task.id, node.id, "再补一个导出按钮")

    assert "steer" in exc_info.value.reason
    send_mock.assert_not_awaited()
    count = len((await db_session.execute(select(Task))).scalars().all())
    assert count == 1, "续聊路径不得新建任务"


async def test_node_chat_blank_text_rejected(db_session: AsyncSession) -> None:
    """空文本拒绝。"""
    node = await _add_node(db_session)
    task = _make_task(TaskStatus.COMPLETED.value)
    db_session.add(task)
    await db_session.commit()

    with pytest.raises(TaskDispatchError):
        await TaskService().node_chat(db_session, task.id, node.id, "   ")


async def test_node_chat_missing_task_not_found(db_session: AsyncSession) -> None:
    """任务不存在返回 TaskNotFoundError。"""
    with pytest.raises(TaskNotFoundError):
        await TaskService().node_chat(db_session, uuid.uuid4(), uuid.uuid4(), "在吗")


async def test_node_chat_missing_node_rejected(db_session: AsyncSession) -> None:
    """目标节点不存在 → BusinessError 422（UT-H1 的服务级语义）。"""
    task = _make_task(TaskStatus.COMPLETED.value)
    db_session.add(task)
    await db_session.commit()

    with pytest.raises(BusinessError) as exc_info:
        await TaskService().node_chat(db_session, task.id, uuid.uuid4(), "在吗")
    assert exc_info.value.status_code == 422
    assert exc_info.value.biz_code == 202001  # AGENT_NODE_NOT_FOUND


async def test_node_chat_offline_node_rejected(db_session: AsyncSession) -> None:
    """节点离线（agent_nodes.status=offline）→ BusinessError 422。"""
    node = await _add_node(db_session, status="offline")
    task = _make_task(TaskStatus.COMPLETED.value)
    db_session.add(task)
    await db_session.commit()

    with pytest.raises(BusinessError) as exc_info:
        await TaskService().node_chat(db_session, task.id, node.id, "在吗")
    assert exc_info.value.status_code == 422
    assert exc_info.value.biz_code == 202002  # AGENT_NODE_OFFLINE
