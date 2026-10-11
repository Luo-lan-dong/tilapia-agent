"""Agent 节点 API 接口测试。"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.agent_node_management import (
    get_agent_node,
    get_agent_node_metrics,
    regenerate_registration_token,
)
from app.api.v1.endpoints.agent_nodes import list_agent_nodes, register_agent_node
from app.core.errors import AgentNotFoundError
from app.main import create_app
from app.models.agent_node import AgentNode
from app.schemas.agent_node import AgentNodeRegisterRequest
from app.services.agent_node_management_service import AgentNodeManagementService
from app.services.agent_node_service import AgentNodeService


async def _noop_background_loop(stop_event: asyncio.Event) -> None:
    """占位后台循环，避免真实调度器/心跳任务占用事件循环。"""
    await stop_event.wait()


def _noop_start_background_task() -> tuple[asyncio.Event, asyncio.Task[None]]:
    """创建占位后台任务，用于替换真实的 lifespan 后台任务。"""
    stop_event = asyncio.Event()
    task = asyncio.create_task(_noop_background_loop(stop_event))
    return stop_event, task


@pytest_asyncio.fixture
async def http_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """创建 HTTP 级异步测试客户端。

    通过 dependency_overrides 将 ``get_async_session`` 替换为测试会话，
    避免 HTTP 请求命中全局 ``AsyncSessionLocal`` 的连接池，防止跨测试的
    事件循环污染。
    """
    from app.api.deps import get_async_session

    async def _override_get_async_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    with (
        patch("app.main.start_scheduler", _noop_start_background_task),
        patch("app.main.start_heartbeat_monitor", _noop_start_background_task),
    ):
        app = create_app()
        app.dependency_overrides[get_async_session] = _override_get_async_session
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


async def test_register_agent_node_success(db_session: AsyncSession) -> None:
    """测试注册 Agent 节点成功，数据正确落库。"""
    request = AgentNodeRegisterRequest(
        name="agent-programming-001",
        agent_type="programming",
        capabilities=["react", "fastapi"],
    )

    response = await register_agent_node(request, db_session, AgentNodeService())

    assert response.code == 0
    assert response.message == "ok"
    assert response.data is not None
    assert response.data.name == "agent-programming-001"
    assert response.data.agent_type == "programming"
    assert response.data.capabilities == ["react", "fastapi"]
    assert response.data.status == "idle"
    assert response.data.load == 0.0
    assert len(response.data.registration_token) == 64

    agent_node = await db_session.get(AgentNode, response.data.id)
    assert agent_node is not None
    assert agent_node.name == "agent-programming-001"
    assert agent_node.agent_type == "programming"
    assert agent_node.status == "idle"
    assert agent_node.load == 0.0
    assert agent_node.registration_token == response.data.registration_token


async def test_register_agent_node_same_client_id_idempotent(
    db_session: AsyncSession,
) -> None:
    """同一 client_id 注册两次仅一条记录，node_id 与 token 保持不变。

    ADR-016 bug 修复后：name 不被 Agent 自报值覆盖（name 由中枢侧管理，
    Agent 重启不能冲掉用户改的 name）；capabilities 仍更新。
    """
    client_id = "host-a-8f3k2m"
    first_request = AgentNodeRegisterRequest(
        name="agent-programming-001",
        agent_type="programming",
        capabilities=["react"],
        client_id=client_id,
    )
    first = await register_agent_node(first_request, db_session, AgentNodeService())

    # 模拟节点重启后再次注册：Agent 自报名变了，能力列表也变了
    second_request = AgentNodeRegisterRequest(
        name="agent-programming-001-renamed",  # Agent 自报名，不应覆盖
        agent_type="programming",
        capabilities=["react", "docker"],
        client_id=client_id,
    )
    second = await register_agent_node(second_request, db_session, AgentNodeService())

    assert first.data is not None
    assert second.data is not None
    assert second.data.id == first.data.id
    assert second.data.registration_token == first.data.registration_token
    # 关键回归：name 保留首次注册值，不被 Agent 重启覆盖
    assert second.data.name == "agent-programming-001"
    # capabilities / agent_type 跟随 Agent 上报更新
    assert second.data.capabilities == ["react", "docker"]
    assert second.data.agent_type == "programming"

    count = await db_session.scalar(select(func.count()).where(AgentNode.client_id == client_id))
    assert count == 1


async def test_register_agent_node_different_client_ids(
    db_session: AsyncSession,
) -> None:
    """不同 client_id 注册生成两条独立记录。"""
    for client_id in ("host-a-8f3k2m", "host-b-9j4n7p"):
        request = AgentNodeRegisterRequest(
            name=f"agent-{client_id}",
            agent_type="programming",
            capabilities=[],
            client_id=client_id,
        )
        response = await register_agent_node(request, db_session, AgentNodeService())
        assert response.data is not None
        assert response.data.id is not None

    count = await db_session.scalar(
        select(func.count()).where(AgentNode.client_id.in_(["host-a-8f3k2m", "host-b-9j4n7p"]))
    )
    assert count == 2


async def test_register_agent_node_empty_client_id_creates_new(
    db_session: AsyncSession,
) -> None:
    """client_id 为空时不做幂等查询，直接创建新记录（兼容旧版 Agent）。

    注意：ADR-016 后 ``agent_type`` 默认值为 ``code``，且同 agent_type 下
    name 必须唯一（partial unique index），所以两次循环用不同 name 避免
    触发唯一约束——这才是「不幂等、各自新建」的真实行为。
    """
    for i in range(2):
        request = AgentNodeRegisterRequest(
            name=f"agent-legacy-{i:03d}",
            agent_type="code",
            capabilities=[],
        )
        response = await register_agent_node(request, db_session, AgentNodeService())
        assert response.data is not None
        assert response.data.id is not None

    count = await db_session.scalar(
        select(func.count()).where(
            (AgentNode.name.like("agent-legacy-%")) & (AgentNode.client_id.is_(None))
        )
    )
    assert count == 2


async def test_register_agent_node_missing_name(http_client: AsyncClient) -> None:
    """HTTP 层测试：缺少 name 返回 422。"""
    response = await http_client.post(
        "/api/v1/agent-nodes/register",
        json={"agent_type": "programming", "capabilities": []},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] != 0
    assert body["data"] is None
    assert "请求参数校验失败" in body["message"]


async def test_register_agent_node_missing_agent_type(http_client: AsyncClient) -> None:
    """HTTP 层测试：缺少 agent_type 返回 422。"""
    response = await http_client.post(
        "/api/v1/agent-nodes/register",
        json={"name": "agent-programming-001", "capabilities": []},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] != 0
    assert body["data"] is None
    assert "请求参数校验失败" in body["message"]


async def _seed_agent_nodes(
    db_session: AsyncSession,
    specs: list[tuple[str, str, str, float]],
) -> None:
    """批量创建测试节点。"""
    for name, agent_type, node_status, load in specs:
        db_session.add(
            AgentNode(
                name=name,
                agent_type=agent_type,
                status=node_status,
                load=load,
                registration_token=f"token_{name}",
            )
        )
    await db_session.commit()


async def test_list_agent_nodes_pagination(db_session: AsyncSession) -> None:
    """测试节点列表分页返回正确结构与总数。"""
    agent_type = "test-node-list-programming"
    specs = [(f"node-{i}", agent_type, "idle", 0.0) for i in range(3)]
    await _seed_agent_nodes(db_session, specs)

    response = await list_agent_nodes(None, agent_type, 2, 0, db_session, AgentNodeService())

    assert response.code == 0
    assert response.data is not None
    assert response.data.pagination.total == 3
    assert len(response.data.items) == 2


async def test_list_agent_nodes_filters(db_session: AsyncSession) -> None:
    """测试按状态与 Agent 类型筛选节点列表。"""
    agent_type = "test-node-list-video"
    await _seed_agent_nodes(
        db_session,
        [
            ("node-idle", agent_type, "idle", 0.0),
            ("node-busy", agent_type, "busy", 0.8),
        ],
    )

    status_response = await list_agent_nodes(
        "busy", agent_type, 50, 0, db_session, AgentNodeService()
    )
    assert status_response.data is not None
    assert status_response.data.pagination.total == 1
    assert status_response.data.items[0].status == "busy"

    type_response = await list_agent_nodes(None, agent_type, 50, 0, db_session, AgentNodeService())
    assert type_response.data is not None
    assert type_response.data.pagination.total == 2


async def test_list_agent_nodes_sort_alive_first_then_load(db_session: AsyncSession) -> None:
    """测试列表排序：存活节点在前，存活节点中负载高的在前，离线节点排底部。"""
    agent_type = "test-node-list-sort"
    await _seed_agent_nodes(
        db_session,
        [
            ("node-offline-a", agent_type, "offline", 0.0),
            ("node-idle-low", agent_type, "idle", 0.1),
            ("node-offline-b", agent_type, "offline", 0.0),
            ("node-busy-high", agent_type, "busy", 0.9),
            ("node-idle-mid", agent_type, "idle", 0.5),
        ],
    )

    response = await list_agent_nodes(None, agent_type, 50, 0, db_session, AgentNodeService())

    assert response.data is not None
    names = [item.name for item in response.data.items]
    assert names == [
        "node-busy-high",
        "node-idle-mid",
        "node-idle-low",
        "node-offline-a",
        "node-offline-b",
    ]


async def test_list_agent_nodes_limit_and_offset(
    db_session: AsyncSession,
) -> None:
    """测试 limit 与 offset 组合分页。"""
    agent_type = "test-node-list-offset"
    specs = [(f"node-{i}", agent_type, "idle", 0.0) for i in range(5)]
    await _seed_agent_nodes(db_session, specs)

    first_page = await list_agent_nodes(None, agent_type, 2, 0, db_session, AgentNodeService())
    second_page = await list_agent_nodes(None, agent_type, 2, 2, db_session, AgentNodeService())

    assert first_page.data is not None
    assert second_page.data is not None
    assert len(first_page.data.items) == 2
    assert len(second_page.data.items) == 2
    assert first_page.data.items[0].id != second_page.data.items[0].id


async def _create_test_agent_node(
    db_session: AsyncSession,
    name: str = "agent-detail-001",
    agent_type: str = "programming",
    node_status: str = "idle",
) -> AgentNode:
    """创建单个测试节点并返回模型实例。"""
    node = AgentNode(
        name=name,
        agent_type=agent_type,
        status=node_status,
        load=0.0,
        registration_token="old_token_value",
    )
    db_session.add(node)
    await db_session.commit()
    await db_session.refresh(node)
    return node


async def test_get_agent_node_success(db_session: AsyncSession) -> None:
    """测试根据 ID 查询节点详情成功。"""
    node = await _create_test_agent_node(db_session)

    response = await get_agent_node(node.id, db_session, AgentNodeManagementService())

    assert response.code == 0
    assert response.message == "ok"
    assert response.data is not None
    assert response.data.id == node.id
    assert response.data.name == node.name
    assert response.data.agent_type == node.agent_type
    assert response.data.status == node.status
    assert response.data.registration_token == node.registration_token


async def test_get_agent_node_not_found(db_session: AsyncSession) -> None:
    """测试查询不存在的节点抛出 AgentNotFoundError。"""
    node_id = uuid.uuid4()

    with pytest.raises(AgentNotFoundError) as exc_info:
        await get_agent_node(node_id, db_session, AgentNodeManagementService())

    assert str(node_id) in str(exc_info.value)


async def test_get_install_script_success(
    http_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP 层测试：获取 Linux code 安装脚本元数据成功（ADR-025 类型绑定）。

    本地缓存清单通过 monkeypatch 注入，不依赖真实 ``data/resources``
    （真实缓存内容随云端同步变化，断言硬编码值会随发布波动）。
    """
    from app.services.resource_sync_service import ResourceSyncService

    def _fake_local_manifest(self: ResourceSyncService) -> dict:
        return {
            "revision": "test-rev",
            "resources": [
                {
                    "key": "install-script-code",
                    "path": "install-scripts/code/install-agent-code-linux.sh",
                    "sha256": "f" * 64,
                    "size": 11418,
                    "version": "0.5.0",
                }
            ],
        }

    monkeypatch.setattr(ResourceSyncService, "local_manifest", _fake_local_manifest)

    response = await http_client.get(
        "/api/v1/agent-nodes/install-scripts",
        params={"agent_type": "code", "os": "linux"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["agent_type"] == "code"
    assert body["data"]["os"] == "linux"
    assert body["data"]["version"] == "0.5.0"
    assert body["data"]["checksum"] == "sha256:" + "f" * 64
    assert "static/install-scripts/code/install-agent-code-linux.sh" in body["data"]["script_url"]
    assert body["data"]["script_url"].startswith(body["data"]["hub_url"])
    assert body["data"]["hub_url"].startswith("http")


async def test_get_install_script_unsupported(http_client: AsyncClient) -> None:
    """HTTP 层测试：不支持的 agent_type/os 返回业务错误。"""
    response = await http_client.get(
        "/api/v1/agent-nodes/install-scripts",
        params={"agent_type": "video", "os": "linux"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] != 0
    assert body["data"] is None
    assert "不支持" in body["message"]


async def test_regenerate_registration_token_success(
    db_session: AsyncSession,
) -> None:
    """测试重新生成注册 Token，落库后与旧 Token 不同。"""
    node = await _create_test_agent_node(db_session)
    old_token = node.registration_token

    response = await regenerate_registration_token(
        node.id, db_session, AgentNodeManagementService()
    )

    assert response.code == 0
    assert response.message == "ok"
    assert response.data is not None
    assert response.data.registration_token != old_token
    assert len(response.data.registration_token) == 64

    refreshed = await db_session.get(AgentNode, node.id)
    assert refreshed is not None
    assert refreshed.registration_token == response.data.registration_token


async def test_regenerate_registration_token_not_found(
    db_session: AsyncSession,
) -> None:
    """测试对不存在节点重置 Token 抛出 AgentNotFoundError。"""
    node_id = uuid.uuid4()

    with pytest.raises(AgentNotFoundError) as exc_info:
        await regenerate_registration_token(node_id, db_session, AgentNodeManagementService())

    assert str(node_id) in str(exc_info.value)


async def test_get_agent_node_metrics_success(db_session: AsyncSession) -> None:
    """测试查询节点监控详情成功，返回最新监控快照。"""
    node = await _create_test_agent_node(db_session, node_status="idle")
    node.metrics_snapshot = {
        "load_score": {"score": 42.5, "level": "normal"},
        "cpu": {"percent": 35.0},
        "memory": {"percent": 50.0},
        "network": {"mb_sent": 1.2},
    }
    await db_session.commit()
    await db_session.refresh(node)

    response = await get_agent_node_metrics(node.id, db_session, AgentNodeManagementService())

    assert response.code == 0
    assert response.message == "ok"
    assert response.data is not None
    assert response.data.node_id == node.id
    assert response.data.is_online is True
    assert response.data.metrics is not None
    assert response.data.metrics["load_score"]["score"] == 42.5


async def test_get_agent_node_metrics_offline(db_session: AsyncSession) -> None:
    """测试离线节点监控详情 is_online 为 false。"""
    node = await _create_test_agent_node(db_session, node_status="offline")

    response = await get_agent_node_metrics(node.id, db_session, AgentNodeManagementService())

    assert response.code == 0
    assert response.data is not None
    assert response.data.is_online is False
    assert response.data.metrics is None


async def test_get_agent_node_metrics_not_found(db_session: AsyncSession) -> None:
    """测试查询不存在节点监控详情抛出 AgentNotFoundError。"""
    node_id = uuid.uuid4()

    with pytest.raises(AgentNotFoundError) as exc_info:
        await get_agent_node_metrics(node_id, db_session, AgentNodeManagementService())

    assert str(node_id) in str(exc_info.value)
