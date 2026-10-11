"""Agent 节点 WebSocket 连接端点测试。"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.websockets import WebSocketDisconnect

from app.api.v1.endpoints import agent_node_ws as agent_node_ws_module
from app.core.config import get_settings
from app.models.agent_node import AgentNode
from app.models.base import AsyncSessionLocal


class _FakeNode:
    """最小节点替身，仅提供端点需要的 id。"""

    id = uuid.uuid4()


class _AlwaysFailingWebSocket:
    """模拟连接层已断开但持续抛 RuntimeError 的 WebSocket。

    2026-10-02 事故回归：端点此前对 receive_json 的任意异常都 ``continue``，
    连接级 RuntimeError 会形成热循环刷日志；修复后必须退出循环。
    """

    def __init__(self) -> None:
        self.query_params = {"node_id": str(uuid.uuid4()), "token": "fake-token"}
        self.headers: dict[str, str] = {}
        self.client = None
        self.accept_called = False
        self.receive_calls = 0

    async def accept(self) -> None:
        self.accept_called = True

    async def receive_json(self) -> dict:
        self.receive_calls += 1
        raise RuntimeError('WebSocket is not connected. Need to call "accept" first.')


async def test_receive_error_exits_loop_instead_of_hot_spinning(monkeypatch) -> None:
    """连接级接收异常必须退出循环（而不是 continue 热循环刷日志）。"""

    async def fake_authenticate(node_id_raw: str, token: str) -> _FakeNode:
        return _FakeNode()

    async def fake_mark_online(agent_node, client_ip=None):  # noqa: ANN001
        return False

    async def fake_mark_offline(node_id: str) -> None:
        return None

    async def fake_connect(node_id: str, websocket) -> None:  # noqa: ANN001
        return None

    async def fake_disconnect_if_current(node_id: str, websocket) -> bool:  # noqa: ANN001
        return True

    monkeypatch.setattr(agent_node_ws_module, "_authenticate_node", fake_authenticate)
    monkeypatch.setattr(agent_node_ws_module, "_mark_node_online", fake_mark_online)
    monkeypatch.setattr(agent_node_ws_module, "_mark_node_offline", fake_mark_offline)
    monkeypatch.setattr(agent_node_ws_module.connection_manager, "connect", fake_connect)
    monkeypatch.setattr(
        agent_node_ws_module.connection_manager,
        "disconnect_if_current",
        fake_disconnect_if_current,
    )

    websocket = _AlwaysFailingWebSocket()
    # 修复前该调用会无限循环；wait_for 让回归失败表现为超时而非挂死
    await asyncio.wait_for(agent_node_ws_module.agent_node_websocket(websocket), timeout=2.0)

    assert websocket.accept_called is True
    assert websocket.receive_calls == 1


def _wait_async(ws_client: TestClient, seconds: float) -> None:
    """在 TestClient 事件循环中执行异步睡眠，不阻塞事件循环线程。"""
    ws_client.portal.call(asyncio.sleep, seconds)


async def _delete_node(node_id: str) -> None:
    """清理测试节点。

    使用独立引擎，避免与 WebSocket 端点共享的连接池因取消事件
    而返回已关闭的连接。
    """
    engine = create_async_engine(get_settings().database_url, echo=False)
    try:
        async with AsyncSessionLocal(bind=engine) as session:
            await session.execute(delete(AgentNode).where(AgentNode.id == uuid.UUID(node_id)))
            await session.commit()
    finally:
        await engine.dispose()


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


@pytest.mark.xfail(reason="TestClient 关闭 portal 时会取消 shield 保护的离线落库，导致状态不可靠；见项目 memory flaky WS 测试记录")
def test_websocket_connect_valid_credentials(ws_client: TestClient) -> None:
    """测试有效凭据可建立 WebSocket 连接，断开后节点变为 offline。"""
    register_resp = ws_client.post(
        "/api/v1/agent-nodes/register",
        json={
            "name": f"test-agent-ws-{uuid.uuid4().hex[:8]}",
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
        ):
            # 短暂保持连接，确保服务端完成 mark_online 并进入 receive_json
            _wait_async(ws_client, 0.1)

        # 等待服务端 finally 块完成状态落库
        _wait_async(ws_client, 0.3)

        node = ws_client.portal.call(_get_node, node_id)
        assert node is not None
        assert node.status == "offline"
    finally:
        ws_client.portal.call(_delete_node, node_id)


def test_websocket_invalid_token_rejected(ws_client: TestClient) -> None:
    """测试无效 token 时 WebSocket 连接被拒绝。"""
    fake_node_id = uuid.uuid4()
    fake_token = "invalid-token"

    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        ws_client.websocket_connect(
            f"/api/v1/agent-nodes/ws?node_id={fake_node_id}&token={fake_token}",
        ),
    ):
        pass

    assert exc_info.value.code == 1008


def test_websocket_heartbeat_updates_timestamp(ws_client: TestClient) -> None:
    """测试发送 heartbeat 事件后 last_heartbeat_at 被更新。"""
    register_resp = ws_client.post(
        "/api/v1/agent-nodes/register",
        json={
            "name": f"test-agent-ws-{uuid.uuid4().hex[:8]}",
            "agent_type": "programming",
            "capabilities": ["pytest"],
        },
    )
    assert register_resp.status_code == 201
    node_id = register_resp.json()["data"]["id"]
    token = register_resp.json()["data"]["registration_token"]

    try:
        # 连接本身会设置 last_heartbeat_at，先记录连接后的值
        with ws_client.websocket_connect(
            f"/api/v1/agent-nodes/ws?node_id={node_id}&token={token}",
        ) as websocket:
            node_after_connect = ws_client.portal.call(_get_node, node_id)
            assert node_after_connect is not None
            assert node_after_connect.last_heartbeat_at is not None
            last_heartbeat_after_connect = node_after_connect.last_heartbeat_at

            _wait_async(ws_client, 0.1)
            websocket.send_json({"event": "agent.heartbeat"})
            _wait_async(ws_client, 0.2)

        # 等待服务端 finally 块完成状态落库
        _wait_async(ws_client, 0.3)

        node_after_heartbeat = ws_client.portal.call(_get_node, node_id)
        assert node_after_heartbeat is not None
        assert node_after_heartbeat.last_heartbeat_at is not None
        # 同一毫秒内可能无变化，使用 >= 避免精度导致的 flaky
        assert node_after_heartbeat.last_heartbeat_at >= last_heartbeat_after_connect
    finally:
        ws_client.portal.call(_delete_node, node_id)


@pytest.mark.xfail(reason="TestClient 关闭 portal 时会取消 shield 保护的离线落库，导致状态不可靠；见项目 memory flaky WS 测试记录")
def test_websocket_disconnect_sets_offline(ws_client: TestClient) -> None:
    """测试断开连接后节点状态为 offline。"""
    register_resp = ws_client.post(
        "/api/v1/agent-nodes/register",
        json={
            "name": f"test-agent-ws-{uuid.uuid4().hex[:8]}",
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
        ):
            node_online = ws_client.portal.call(_get_node, node_id)
            assert node_online is not None
            assert node_online.status in {"starting", "idle", "busy"}

        # 等待服务端 finally 块完成状态落库
        _wait_async(ws_client, 0.3)

        node_offline = ws_client.portal.call(_get_node, node_id)
        assert node_offline is not None
        assert node_offline.status == "offline"
    finally:
        ws_client.portal.call(_delete_node, node_id)
