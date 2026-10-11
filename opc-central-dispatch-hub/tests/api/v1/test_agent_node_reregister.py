"""节点重注册场景：name 保护、capabilities 更新（ADR-016 bug 修复回归）。

场景：Agent 进程重启时会按 client_id 幂等注册。该测试套件保证
``register_agent_node`` 在幂等路径中：
- 不覆盖中枢侧的 name（用户在 NodeDetailDrawer 改过的不被 Agent 自报冲掉）
- 仍更新 agent_type 与 capabilities（这两项是 Agent 配置）
- 不重置 status / load / registration_token
"""

from __future__ import annotations

import uuid

import pytest

from app.schemas.agent_node import AgentNodeRegisterRequest
from app.services.agent_node_service import AgentNodeService

pytestmark = pytest.mark.asyncio


async def _create_node_with_name(
    db_session, *, name: str, client_id: str | None, agent_type: str = "code"
):
    """创建节点（首次注册），返回 ORM 实例。"""
    service = AgentNodeService()
    request = AgentNodeRegisterRequest(
        name=name,
        agent_type=agent_type,
        capabilities=["react", "fastapi"],
        client_id=client_id,
    )
    response = await service.register_agent_node(db_session, request)
    return response


async def test_reregister_does_not_overwrite_name(
    db_session,
) -> None:
    """回归测试：用户改名后，Agent 重启不应冲掉（ADR-016 bug 修复）。"""
    client_id = f"client-{uuid.uuid4().hex[:8]}"
    # 1) 首次注册：使用 Agent 自报 name
    initial = await _create_node_with_name(
        db_session, name="localhost-programming-aaa", client_id=client_id
    )
    assert initial.name == "localhost-programming-aaa"

    # 2) 中枢侧改 name（模拟用户在 NodeDetailDrawer 改了名）
    from app.schemas.agent_node import AgentNodeUpdateRequest
    from app.services.agent_node_management_service import (
        AgentNodeManagementService,
    )

    mgmt = AgentNodeManagementService()
    await mgmt.update_node_name(
        db_session,
        initial.id,
        AgentNodeUpdateRequest(name="我的笔记本"),
    )

    # 3) Agent 重启：自报名变了，但 client_id 相同
    service = AgentNodeService()
    request = AgentNodeRegisterRequest(
        name="localhost-programming-bbb-new-random",  # 不同的 Agent 自报名
        agent_type="code",
        capabilities=["react", "fastapi"],
        client_id=client_id,
    )
    after = await service.register_agent_node(db_session, request)

    # 4) 用户的"我的笔记本"必须保留
    assert after.id == initial.id
    assert after.name == "我的笔记本", "Agent 重启不能覆盖用户在中枢改的 name"


async def test_reregister_still_updates_agent_type_and_capabilities(
    db_session,
) -> None:
    """agent_type / capabilities 仍然按 Agent 上报更新（这两项是 Agent 配置）。"""
    client_id = f"client-{uuid.uuid4().hex[:8]}"
    initial = await _create_node_with_name(
        db_session,
        name="my-node",
        client_id=client_id,
        agent_type="code",
    )
    assert initial.agent_type == "code"
    assert initial.capabilities == ["react", "fastapi"]

    # 用户改名锁定 name 不变
    from app.schemas.agent_node import AgentNodeUpdateRequest
    from app.services.agent_node_management_service import (
        AgentNodeManagementService,
    )

    mgmt = AgentNodeManagementService()
    await mgmt.update_node_name(db_session, initial.id, AgentNodeUpdateRequest(name="固定名"))

    # Agent 重启并切换 agent_type（理论上不该切，但 service 应跟随）
    service = AgentNodeService()
    request = AgentNodeRegisterRequest(
        name="Agent-self-name-should-be-ignored",
        agent_type="video",  # 切换类型
        capabilities=["video-edit", "subtitle"],  # 切换能力
        client_id=client_id,
    )
    after = await service.register_agent_node(db_session, request)

    # name 保留中枢值
    assert after.name == "固定名"
    # agent_type 与 capabilities 跟随 Agent 上报（service 行为）
    assert after.agent_type == "video"
    assert after.capabilities == ["video-edit", "subtitle"]


async def test_reregister_does_not_reset_status_load_token(
    db_session,
) -> None:
    """status / load / registration_token 不被重置（避免误重置在用节点）。"""
    from sqlalchemy import select

    from app.models.agent_node import AgentNode, NodeStatus

    client_id = f"client-{uuid.uuid4().hex[:8]}"
    initial = await _create_node_with_name(db_session, name="my-node", client_id=client_id)
    # 直接 SQL 修改状态：模拟任务跑起来
    node = (
        await db_session.execute(select(AgentNode).where(AgentNode.id == initial.id))
    ).scalar_one()
    node.status = NodeStatus.BUSY.value
    node.load = 1.0
    original_token = node.registration_token
    await db_session.commit()

    # Agent 重启
    service = AgentNodeService()
    request = AgentNodeRegisterRequest(
        name="Agent-self-name-ignored",
        agent_type="code",
        capabilities=[],
        client_id=client_id,
    )
    after = await service.register_agent_node(db_session, request)

    # status / load / token 保持
    assert after.status == NodeStatus.BUSY.value
    assert after.load == 1.0
    assert after.registration_token == original_token


async def test_register_without_client_id_uses_request_name(
    db_session,
) -> None:
    """不传 client_id 的兼容路径（old Agent）：用 Agent 自报的 name 创建新行。"""
    # 不传 client_id 走"创建新行"分支，name 应该直接使用 request.name
    service = AgentNodeService()
    request = AgentNodeRegisterRequest(
        name="legacy-name",
        agent_type="code",
        capabilities=[],
        client_id=None,
    )
    response = await service.register_agent_node(db_session, request)
    assert response.name == "legacy-name"
