"""任务全生命周期集成测试。

覆盖调度器通过 WebSocket 下发任务，以及节点上报 agent.task_accepted /
agent.task_rejected / agent.task_progress / agent.task_completed 后的状态流转。
"""

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.models.agent_node import AgentNode
from app.models.base import AsyncSessionLocal
from app.models.dispatch_decision import DispatchDecision
from app.models.task import Task
from app.services.scheduler import dispatch_pending_tasks


def _wait_async(ws_client: TestClient, seconds: float) -> None:
    """在 TestClient 事件循环中执行异步睡眠，不阻塞事件循环线程。"""
    ws_client.portal.call(asyncio.sleep, seconds)


async def _get_node(node_id: str) -> AgentNode | None:
    """通过主键查询节点。

    使用独立引擎，避免与 WebSocket 端点共享的连接池因取消事件
    而返回已关闭的连接。
    """
    engine = create_async_engine(get_settings().database_url, echo=False)
    try:
        async with AsyncSessionLocal(bind=engine) as session:
            return await session.get(AgentNode, uuid.UUID(node_id))
    finally:
        await engine.dispose()


async def _delete_node(node_id: str) -> None:
    """清理测试节点。

    使用独立引擎，避免与 WebSocket 端点共享的连接池因取消事件
    而返回已关闭的连接。删除节点前先清除关联任务的 dispatch_decisions
    （ADR-033 决策审计行外键引用 tasks）与任务本身，避免外键冲突。
    """
    engine = create_async_engine(get_settings().database_url, echo=False)
    try:
        async with AsyncSessionLocal(bind=engine) as session:
            node_task_ids = select(Task.id).where(
                Task.assigned_node_id == uuid.UUID(node_id)
            )
            await session.execute(
                delete(DispatchDecision).where(DispatchDecision.task_id.in_(node_task_ids)),
            )
            await session.execute(
                delete(Task).where(Task.assigned_node_id == uuid.UUID(node_id)),
            )
            await session.execute(
                delete(AgentNode).where(AgentNode.id == uuid.UUID(node_id)),
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _create_pending_task(agent_type: str) -> str:
    """创建 pending 任务并返回 ID。

    使用独立引擎，避免与 WebSocket 端点共享的连接池因取消事件
    而返回已关闭的连接。
    """
    engine = create_async_engine(get_settings().database_url, echo=False)
    try:
        async with AsyncSessionLocal(bind=engine) as session:
            task = Task(
                title=f"test-task-{uuid.uuid4().hex}",
                agent_type=agent_type,
                status="pending",
                progress=0,
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return str(task.id)
    finally:
        await engine.dispose()


async def _get_task(task_id: str) -> Task | None:
    """通过主键查询任务。

    使用独立引擎，避免与 WebSocket 端点共享的连接池因取消事件
    而返回已关闭的连接。
    """
    engine = create_async_engine(get_settings().database_url, echo=False)
    try:
        async with AsyncSessionLocal(bind=engine) as session:
            return await session.get(Task, uuid.UUID(task_id))
    finally:
        await engine.dispose()


async def _delete_task(task_id: str) -> None:
    """删除测试任务。

    使用独立引擎，避免与 WebSocket 端点共享的连接池因取消事件
    而返回已关闭的连接。先清理引用任务的 dispatch_decisions（ADR-033
    决策审计行外键引用 tasks），避免删除任务时外键违约。
    """
    engine = create_async_engine(get_settings().database_url, echo=False)
    try:
        async with AsyncSessionLocal(bind=engine) as session:
            task = await session.get(Task, uuid.UUID(task_id))
            if task is not None:
                await session.execute(
                    delete(DispatchDecision).where(
                        DispatchDecision.task_id == uuid.UUID(task_id)
                    )
                )
                await session.delete(task)
                await session.commit()
    finally:
        await engine.dispose()


async def _dispatch_pending_tasks() -> int:
    """触发任务调度器一次。

    使用独立引擎，避免与 WebSocket 端点共享的连接池因取消事件
    而返回已关闭的连接。
    """
    engine = create_async_engine(get_settings().database_url, echo=False)
    try:
        async with AsyncSessionLocal(bind=engine) as session:
            return await dispatch_pending_tasks(session)
    finally:
        await engine.dispose()


def test_task_lifecycle_accepted_and_completed(ws_client: TestClient) -> None:
    """测试 pending -> assigned -> running -> progress -> completed 完整流转。"""
    register_resp = ws_client.post(
        "/api/v1/agent-nodes/register",
        json={
            "name": f"test-agent-lifecycle-{uuid.uuid4().hex[:8]}",
            "agent_type": "programming",
            "capabilities": ["pytest"],
        },
    )
    assert register_resp.status_code == 201
    node_id = register_resp.json()["data"]["id"]
    token = register_resp.json()["data"]["registration_token"]
    task_id: str | None = None

    try:
        with ws_client.websocket_connect(
            f"/api/v1/agent-nodes/ws?node_id={node_id}&token={token}",
        ) as websocket:
            task_id = ws_client.portal.call(_create_pending_task, "programming")
            ws_client.portal.call(_dispatch_pending_tasks)

            message = websocket.receive_json()
            assert message["event"] == "hub.task_dispatch"
            assert message["payload"]["task_id"] == task_id
            assert "task_name" in message["payload"]
            # 权限策略（ADR-061 M2b / IT-A1）：派发必须携带策略，节点据此落地白名单
            assert message["payload"]["permission_policy"]["mode"] in ("unattended", "interactive")
            assert isinstance(message["payload"]["permission_policy"]["allow"], list)

            websocket.send_json(
                {"event": "agent.task_accepted", "payload": {"task_id": task_id}},
            )
            _wait_async(ws_client, 0.2)
            task = ws_client.portal.call(_get_task, task_id)
            assert task is not None
            assert task.status == "running"

            websocket.send_json(
                {"event": "agent.task_progress", "payload": {"task_id": task_id, "progress": 50}},
            )
            _wait_async(ws_client, 0.2)
            task = ws_client.portal.call(_get_task, task_id)
            assert task is not None
            assert task.progress == 50

            websocket.send_json(
                {"event": "agent.task_completed", "payload": {"task_id": task_id}},
            )
            _wait_async(ws_client, 0.2)
            task = ws_client.portal.call(_get_task, task_id)
            assert task is not None
            assert task.status == "completed"
            assert task.progress == 100

            node = ws_client.portal.call(_get_node, node_id)
            assert node is not None
            assert node.status == "idle"
            assert node.load == 0.0
    finally:
        if task_id:
            ws_client.portal.call(_delete_task, task_id)
        ws_client.portal.call(_delete_node, node_id)


def test_task_lifecycle_rejected_back_to_pending(ws_client: TestClient) -> None:
    """测试 pending -> assigned -> rejected -> pending 状态回退。"""
    register_resp = ws_client.post(
        "/api/v1/agent-nodes/register",
        json={
            "name": f"test-agent-rejected-{uuid.uuid4().hex[:8]}",
            "agent_type": "programming",
            "capabilities": ["pytest"],
        },
    )
    assert register_resp.status_code == 201
    node_id = register_resp.json()["data"]["id"]
    token = register_resp.json()["data"]["registration_token"]
    task_id: str | None = None

    try:
        with ws_client.websocket_connect(
            f"/api/v1/agent-nodes/ws?node_id={node_id}&token={token}",
        ) as websocket:
            task_id = ws_client.portal.call(_create_pending_task, "programming")
            ws_client.portal.call(_dispatch_pending_tasks)

            message = websocket.receive_json()
            assert message["event"] == "hub.task_dispatch"
            assert message["payload"]["task_id"] == task_id
            # 权限策略（ADR-061 M2b）：被拒单的派发同样携带策略（重派时节点不必回拉）
            assert "permission_policy" in message["payload"]

            websocket.send_json(
                {"event": "agent.task_rejected", "payload": {"task_id": task_id}},
            )
            _wait_async(ws_client, 0.2)
            task = ws_client.portal.call(_get_task, task_id)
            assert task is not None
            assert task.status == "pending"
            assert task.assigned_node_id is None
            assert task.progress == 0

            node = ws_client.portal.call(_get_node, node_id)
            assert node is not None
            assert node.status == "idle"
            assert node.load == 0.0
    finally:
        if task_id:
            ws_client.portal.call(_delete_task, task_id)
        ws_client.portal.call(_delete_node, node_id)


def test_websocket_unknown_event_ignored(ws_client: TestClient) -> None:
    """测试未知事件被忽略且连接不中断。"""
    register_resp = ws_client.post(
        "/api/v1/agent-nodes/register",
        json={
            "name": f"test-agent-unknown-{uuid.uuid4().hex[:8]}",
            "agent_type": "programming",
            "capabilities": ["pytest"],
        },
    )
    assert register_resp.status_code == 201
    node_id = register_resp.json()["data"]["id"]
    token = register_resp.json()["data"]["registration_token"]

    try:
        with ws_client.websocket_connect(
            f"/api/v1/agent-nodes/ws?node_id={node_id}&token={token}",
        ) as websocket:
            websocket.send_json({"event": "unknown_event", "payload": {"foo": "bar"}})
            _wait_async(ws_client, 0.1)
            # 再次发送心跳，确认连接未被关闭
            websocket.send_json({"event": "agent.heartbeat"})
            _wait_async(ws_client, 0.1)
    finally:
        ws_client.portal.call(_delete_node, node_id)


def test_websocket_malformed_event_ignored(ws_client: TestClient) -> None:
    """测试格式错误的事件被忽略且连接不中断。"""
    register_resp = ws_client.post(
        "/api/v1/agent-nodes/register",
        json={
            "name": f"test-agent-malformed-{uuid.uuid4().hex[:8]}",
            "agent_type": "programming",
            "capabilities": ["pytest"],
        },
    )
    assert register_resp.status_code == 201
    node_id = register_resp.json()["data"]["id"]
    token = register_resp.json()["data"]["registration_token"]

    try:
        with ws_client.websocket_connect(
            f"/api/v1/agent-nodes/ws?node_id={node_id}&token={token}",
        ) as websocket:
            websocket.send_text("not-valid-json")
            _wait_async(ws_client, 0.1)
            # 再次发送心跳，确认连接未被关闭
            websocket.send_json({"event": "agent.heartbeat"})
            _wait_async(ws_client, 0.1)
    finally:
        ws_client.portal.call(_delete_node, node_id)
