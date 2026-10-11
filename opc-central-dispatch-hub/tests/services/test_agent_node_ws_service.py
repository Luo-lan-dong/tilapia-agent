"""节点心跳处理单元测试。

通过 mock ``AsyncSessionLocal`` 与数据库会话，验证 ADR-032 的节点状态
派生语义：busy 为纯展示态，心跳上报的 starting/idle/busy 均直接同步，
任务失败/完成后 Agent 心跳上报 idle 即自动恢复空闲（修复 sticky busy）。
不依赖真实数据库。
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.agent_node import AgentNode, NodeStatus
from app.services.agent_node_ws_service import _handle_heartbeat


def _make_node(status: str) -> AgentNode:
    """构造未落库的节点实例，用于 mock 查询返回。"""
    return AgentNode(
        id=uuid.uuid4(),
        name="测试节点",
        agent_type="programming",
        capabilities=["pytest"],
        status=status,
        load=1.0,
        registration_token="test-token",
    )


def _patch_db_session(node: AgentNode) -> AsyncMock:
    """将处理器内部的 AsyncSessionLocal 替换为 mock 会话工厂。"""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = node
    session.execute.return_value = result

    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = session
    factory = MagicMock(return_value=session_ctx)
    return patch("app.services.agent_node_ws_service.AsyncSessionLocal", factory)


async def test_heartbeat_idle_report_recovers_busy_node() -> None:
    """busy 节点心跳上报 idle：状态恢复 idle（sticky busy 回归测试）。"""
    node = _make_node(NodeStatus.BUSY.value)

    with _patch_db_session(node):
        await _handle_heartbeat(str(node.id), {"status": NodeStatus.IDLE.value})

    assert node.status == NodeStatus.IDLE.value


async def test_heartbeat_busy_report_marks_busy_node() -> None:
    """idle 节点心跳上报 busy（有在跑任务）：状态同步为 busy。"""
    node = _make_node(NodeStatus.IDLE.value)

    with _patch_db_session(node):
        await _handle_heartbeat(str(node.id), {"status": NodeStatus.BUSY.value})

    assert node.status == NodeStatus.BUSY.value


async def test_heartbeat_starting_report_syncs_status() -> None:
    """节点重启后心跳上报 starting：状态同步为 starting。"""
    node = _make_node(NodeStatus.OFFLINE.value)

    with _patch_db_session(node):
        await _handle_heartbeat(str(node.id), {"status": NodeStatus.STARTING.value})

    assert node.status == NodeStatus.STARTING.value


async def test_heartbeat_unknown_status_keeps_current() -> None:
    """心跳上报非法状态值：保持节点当前状态不变。"""
    node = _make_node(NodeStatus.BUSY.value)

    with _patch_db_session(node):
        await _handle_heartbeat(str(node.id), {"status": "not-a-status"})

    assert node.status == NodeStatus.BUSY.value


async def test_heartbeat_missing_status_keeps_current() -> None:
    """心跳不携带 status 字段（旧版 Agent）：保持节点当前状态不变。"""
    node = _make_node(NodeStatus.IDLE.value)

    with _patch_db_session(node):
        await _handle_heartbeat(str(node.id), {})

    assert node.status == NodeStatus.IDLE.value
