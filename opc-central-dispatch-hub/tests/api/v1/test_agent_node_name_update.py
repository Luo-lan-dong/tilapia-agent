"""Agent 节点 PATCH name + install-script agent_type=code 测试（ADR-016）。"""

import logging
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AgentNotFoundError, BusinessError, ErrorCode
from app.main import create_app
from app.models.agent_node import AgentNode, NodeStatus
from app.schemas.agent_node import AgentNodeUpdateRequest
from app.services.agent_node_management_service import AgentNodeManagementService

pytestmark = pytest.mark.asyncio


# -------------------------- Service 单元 / 集成 -------------------------- #


async def _create_node(
    db_session: AsyncSession,
    *,
    name: str = "old-name",
    agent_type: str = "code",
    status: str = NodeStatus.IDLE.value,
) -> AgentNode:
    node = AgentNode(
        name=name,
        agent_type=agent_type,
        status=status,
        load=0.0,
        registration_token=f"token_{uuid.uuid4().hex[:8]}",
    )
    db_session.add(node)
    await db_session.commit()
    await db_session.refresh(node)
    return node


async def test_update_node_name_happy_path(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """PATCH /agent-nodes/{id} 改 name 成功，DB 落库，输出 node_name_updated 日志。"""
    node = await _create_node(db_session, name="old", agent_type="code")
    service = AgentNodeManagementService()

    with caplog.at_level(logging.INFO, logger="app.services.agent_node_management_service"):
        updated = await service.update_node_name(
            db_session,
            node.id,
            AgentNodeUpdateRequest(name="new"),
        )

    assert updated.id == node.id
    assert updated.name == "new"
    assert updated.agent_type == "code"

    row = (
        await db_session.execute(select(AgentNode.name).where(AgentNode.id == node.id))
    ).scalar_one()
    assert row == "new"

    assert any(rec.message == "node_name_updated" for rec in caplog.records), (
        "expected node_name_updated log"
    )


async def test_update_node_name_rejects_blank(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """name 是纯空白时返回 202011，DB 未变，输出 reason=invalid 日志。"""
    node = await _create_node(db_session, name="stable", agent_type="code")

    with (
        caplog.at_level(logging.WARNING, logger="app.services.agent_node_management_service"),
        pytest.raises(BusinessError) as exc_info,
    ):
        await service_update(db_session, node.id, "   ")

    assert exc_info.value.biz_code == ErrorCode.AGENT_NODE_NAME_INVALID
    assert exc_info.value.status_code == 422

    row = (
        await db_session.execute(select(AgentNode.name).where(AgentNode.id == node.id))
    ).scalar_one()
    assert row == "stable"
    assert any(
        rec.message == "node_name_update_rejected" and getattr(rec, "reason", None) == "invalid"
        for rec in caplog.records
    ), "expected reason=invalid log"


async def test_update_node_name_rejects_duplicate_in_same_agent_type(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """同 agent_type 下 name 重复返回 202010，DB 未变，输出 reason=duplicate 日志。"""
    await _create_node(db_session, name="alpha", agent_type="code")
    b = await _create_node(db_session, name="bravo", agent_type="code")

    with (
        caplog.at_level(logging.WARNING, logger="app.services.agent_node_management_service"),
        pytest.raises(BusinessError) as exc_info,
    ):
        await service_update(db_session, b.id, "alpha")

    assert exc_info.value.biz_code == ErrorCode.AGENT_NODE_NAME_DUPLICATE
    assert exc_info.value.status_code == 422

    row_b = (
        await db_session.execute(select(AgentNode.name).where(AgentNode.id == b.id))
    ).scalar_one()
    assert row_b == "bravo"
    assert any(
        rec.message == "node_name_update_rejected" and getattr(rec, "reason", None) == "duplicate"
        for rec in caplog.records
    ), "expected reason=duplicate log"


async def test_update_node_name_allows_same_name_across_different_agent_type(
    db_session: AsyncSession,
) -> None:
    """不同 agent_type 下 name 可以相同（partial unique 行为）。"""
    await _create_node(db_session, name="shared", agent_type="code")
    other = await _create_node(db_session, name="other", agent_type="video")

    service = AgentNodeManagementService()
    updated = await service.update_node_name(
        db_session,
        other.id,
        AgentNodeUpdateRequest(name="shared"),
    )
    assert updated.name == "shared"


async def test_update_node_name_not_found(db_session: AsyncSession) -> None:
    """不存在的 node_id 抛 AgentNotFoundError。"""
    service = AgentNodeManagementService()
    with pytest.raises(AgentNotFoundError):
        await service.update_node_name(
            db_session,
            uuid.uuid4(),
            AgentNodeUpdateRequest(name="x"),
        )


async def test_update_node_name_idempotent(db_session: AsyncSession) -> None:
    """把 name 改回原值：视为成功（不报错），不写无意义 UPDATE。"""
    node = await _create_node(db_session, name="same", agent_type="code")
    service = AgentNodeManagementService()
    updated = await service.update_node_name(
        db_session,
        node.id,
        AgentNodeUpdateRequest(name="same"),
    )
    assert updated.name == "same"


async def service_update(db_session: AsyncSession, node_id: uuid.UUID, name: str) -> object:
    """便捷 helper：直接调 Service.update_node_name。"""
    service = AgentNodeManagementService()
    return await service.update_node_name(db_session, node_id, AgentNodeUpdateRequest(name=name))


# -------------------------- HTTP / API 契约 -------------------------- #


@pytest_asyncio.fixture
async def http_client(
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncClient, None]:
    """HTTP 测试客户端。"""
    from app.api.deps import get_async_session

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    with (
        patch("app.main.start_scheduler", _noop),
        patch("app.main.start_heartbeat_monitor", _noop),
    ):
        app = create_app()
        app.dependency_overrides[get_async_session] = _override
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


def _noop(*_args, **_kwargs):  # pragma: no cover - simple noop
    import asyncio

    return asyncio.Event(), None


async def test_patch_agent_node_200_returns_full_response(
    http_client: AsyncClient, db_session: AsyncSession
) -> None:
    """HTTP 200 响应字段集 = AgentNodeResponse 字段集，agent_type 保持 'code'。"""
    node = await _create_node(db_session, name="initial", agent_type="code")

    response = await http_client.patch(
        f"/api/v1/agent-nodes/{node.id}",
        json={"name": "renamed"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["id"] == str(node.id)
    assert data["name"] == "renamed"
    assert data["agent_type"] == "code"
    # 关键字段未泄漏给客户端修改
    assert data["registration_token"] == node.registration_token


async def test_patch_agent_node_422_blank(
    http_client: AsyncClient, db_session: AsyncSession
) -> None:
    node = await _create_node(db_session, name="x", agent_type="code")
    response = await http_client.patch(
        f"/api/v1/agent-nodes/{node.id}",
        json={"name": "   "},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == ErrorCode.AGENT_NODE_NAME_INVALID


async def test_patch_agent_node_422_duplicate(
    http_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_node(db_session, name="alpha", agent_type="code")
    b = await _create_node(db_session, name="bravo", agent_type="code")
    response = await http_client.patch(
        f"/api/v1/agent-nodes/{b.id}",
        json={"name": "alpha"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == ErrorCode.AGENT_NODE_NAME_DUPLICATE


async def test_patch_agent_node_404(
    http_client: AsyncClient,
) -> None:
    response = await http_client.patch(
        f"/api/v1/agent-nodes/{uuid.uuid4()}",
        json={"name": "x"},
    )
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == ErrorCode.AGENT_NODE_NOT_FOUND


async def test_patch_agent_node_ignores_client_id_in_payload(
    http_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Pydantic schema 不含 client_id：即使客户端传也被丢弃。"""
    node = await _create_node(db_session, name="stable", agent_type="code")
    response = await http_client.patch(
        f"/api/v1/agent-nodes/{node.id}",
        json={"name": "renamed", "client_id": "evil", "agent_type": "video"},
    )
    assert response.status_code == 200
    body = response.json()
    # client_id 未被 Pydantic 接受，但后端不会因额外字段报错；
    # 关键是 agent_type 没被改成 video
    assert body["data"]["agent_type"] == "code"
    assert body["data"]["name"] == "renamed"

    row = (
        await db_session.execute(
            select(AgentNode.client_id, AgentNode.agent_type).where(AgentNode.id == node.id)
        )
    ).first()
    # 客户端伪造的 client_id 不会被写入
    assert row[0] is None
    assert row[1] == "code"


async def test_get_install_script_supports_code_linux(
    http_client: AsyncClient,
) -> None:
    response = await http_client.get(
        "/api/v1/agent-nodes/install-scripts",
        params={"agent_type": "code", "os": "linux"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["agent_type"] == "code"


async def test_get_install_script_rejects_other_agent_type(
    http_client: AsyncClient,
) -> None:
    response = await http_client.get(
        "/api/v1/agent-nodes/install-scripts",
        params={"agent_type": "video", "os": "linux"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == ErrorCode.INSTALL_SCRIPT_NOT_SUPPORTED
