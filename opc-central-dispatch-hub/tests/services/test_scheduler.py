"""任务调度器服务测试。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_node import AgentNode, NodeStatus
from app.models.task import Task, TaskStatus
from app.services.scheduler import _build_dispatch_message, dispatch_pending_tasks


def _unique_token() -> str:
    """生成唯一的注册令牌。"""
    return f"token-{uuid.uuid4().hex}"


async def _create_agent_node(
    session: AsyncSession,
    agent_type: str,
    status: str,
    load: float = 0.0,
) -> AgentNode:
    """创建测试用的 Agent 节点。"""
    node = AgentNode(
        name=f"test-agent-{uuid.uuid4().hex}",
        agent_type=agent_type,
        status=status,
        load=load,
        registration_token=_unique_token(),
    )
    session.add(node)
    await session.commit()
    await session.refresh(node)
    return node


async def _create_task(
    session: AsyncSession,
    agent_type: str,
    status: str,
) -> Task:
    """创建测试用的任务。"""
    task = Task(
        title=f"test-task-{uuid.uuid4().hex}",
        agent_type=agent_type,
        status=status,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """每个测试前清空任务与节点表，避免历史数据干扰调度计数。"""
    await db_session.execute(delete(Task))
    await db_session.execute(delete(AgentNode))
    yield


async def test_dispatch_pending_tasks_with_matching_node(db_session: AsyncSession) -> None:
    """存在匹配的空闲节点时，pending 任务应被成功分配。"""
    node = await _create_agent_node(db_session, "programming", NodeStatus.IDLE.value)
    task = await _create_task(db_session, "programming", TaskStatus.PENDING.value)

    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 1

    refreshed_task = await db_session.get(Task, task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == TaskStatus.ASSIGNED.value
    assert refreshed_task.assigned_node_id == node.id

    refreshed_node = await db_session.get(AgentNode, node.id)
    assert refreshed_node is not None
    # ADR-032：节点状态/负载由心跳派生，不再随任务分配改写
    assert refreshed_node.status == NodeStatus.IDLE.value
    assert refreshed_node.load == 0.0


async def test_dispatch_pending_tasks_no_matching_node(db_session: AsyncSession) -> None:
    """无匹配节点时，pending 任务应保持不变。"""
    await _create_agent_node(db_session, "programming", NodeStatus.IDLE.value)
    task = await _create_task(db_session, "design", TaskStatus.PENDING.value)

    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 0

    refreshed_task = await db_session.get(Task, task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == TaskStatus.PENDING.value
    assert refreshed_task.assigned_node_id is None


async def test_dispatch_pending_tasks_selects_lowest_load(db_session: AsyncSession) -> None:
    """多个匹配节点时应选择 load 最低的节点。"""
    high_load_node = await _create_agent_node(
        db_session,
        "programming",
        NodeStatus.IDLE.value,
        load=2.0,
    )
    lowest_load_node = await _create_agent_node(
        db_session,
        "programming",
        NodeStatus.IDLE.value,
        load=0.0,
    )
    medium_load_node = await _create_agent_node(
        db_session,
        "programming",
        NodeStatus.IDLE.value,
        load=1.0,
    )
    task = await _create_task(db_session, "programming", TaskStatus.PENDING.value)

    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 1

    refreshed_task = await db_session.get(Task, task.id)
    assert refreshed_task is not None
    assert refreshed_task.assigned_node_id == lowest_load_node.id

    refreshed_lowest = await db_session.get(AgentNode, lowest_load_node.id)
    assert refreshed_lowest is not None
    # ADR-032：节点状态/负载由心跳派生，不再随任务分配改写
    assert refreshed_lowest.status == NodeStatus.IDLE.value
    assert refreshed_lowest.load == 0.0

    for node_id in (high_load_node.id, medium_load_node.id):
        other_node = await db_session.get(AgentNode, node_id)
        assert other_node is not None
        assert other_node.status == NodeStatus.IDLE.value


async def test_dispatch_pending_tasks_ignores_non_pending_tasks(
    db_session: AsyncSession,
) -> None:
    """非 pending 状态的任务不应被分配。"""
    node = await _create_agent_node(db_session, "programming", NodeStatus.IDLE.value)
    running_task = await _create_task(db_session, "programming", TaskStatus.RUNNING.value)
    running_task.assigned_node_id = node.id
    await db_session.commit()

    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 0

    refreshed_task = await db_session.get(Task, running_task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == TaskStatus.RUNNING.value
    assert refreshed_task.assigned_node_id == node.id

    refreshed_node = await db_session.get(AgentNode, node.id)
    assert refreshed_node is not None
    assert refreshed_node.status == NodeStatus.IDLE.value


def test_build_dispatch_message_with_root_task_id() -> None:
    """迭代链任务派发消息：base_task_id 取链根、change_brief 原样透传。"""
    root_id = uuid.uuid4()
    task = Task(
        id=uuid.uuid4(),
        title="迭代任务",
        agent_type="programming",
        status=TaskStatus.ASSIGNED.value,
        root_task_id=root_id,
        change_brief="登录页新增记住我复选框",
    )

    message = _build_dispatch_message(task)
    payload = message["payload"]

    assert payload["base_task_id"] == str(root_id)
    assert payload["change_brief"] == "登录页新增记住我复选框"
    assert payload["task_id"] == str(task.id)


def test_build_dispatch_message_without_root_task_id() -> None:
    """无迭代链任务派发消息：base_task_id 回退为任务自身 ID、change_brief 为空。"""
    task = Task(
        id=uuid.uuid4(),
        title="普通任务",
        agent_type="programming",
        status=TaskStatus.ASSIGNED.value,
    )

    message = _build_dispatch_message(task)
    payload = message["payload"]

    assert payload["base_task_id"] == str(task.id)
    assert payload["change_brief"] is None


# ---------------- ADR-059 D2/D3：指定节点排队、无自主节点不静默、强行派送 ----------------


async def _set_metrics(
    session: AsyncSession,
    node: AgentNode,
    *,
    available_gb: float | None,
    load_score: float | None,
) -> None:
    """写入节点心跳 metrics（准入双门槛判据来源）。"""
    node.metrics_snapshot = {
        "memory": {"percent": 40.0, "available_gb": available_gb},
        "load_score": {"score": load_score},
        "cpu": {"percent": 10.0},
    }
    await session.commit()


async def test_no_eligible_node_holds_task_with_reason(
    db_session: AsyncSession,
) -> None:
    """UT-10 守卫：无「可自主」节点时**不派发**并写下排队原因（不静默）。"""
    node = await _create_agent_node(db_session, "programming", NodeStatus.IDLE.value)
    await _set_metrics(db_session, node, available_gb=0.6, load_score=30.0)
    task = await _create_task(db_session, "programming", TaskStatus.PENDING.value)

    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 0
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.PENDING.value
    assert refreshed.assigned_node_id is None
    # 排队原因含节点名与缺口数值，用户能看到「在等哪台、缺什么」
    assert refreshed.dispatch_hold_reason is not None
    assert "可用内存" in refreshed.dispatch_hold_reason
    assert "0.6GB" in refreshed.dispatch_hold_reason


async def test_eligible_multi_node_still_dispatches_lowest(
    db_session: AsyncSession,
) -> None:
    """回归守卫：双门槛不得误伤正常派发（都达标 → 仍按负载最低派）。"""
    blocked = await _create_agent_node(db_session, "programming", NodeStatus.IDLE.value, load=0.0)
    await _set_metrics(db_session, blocked, available_gb=0.5, load_score=1.0)
    healthy = await _create_agent_node(db_session, "programming", NodeStatus.IDLE.value, load=1.0)
    await _set_metrics(db_session, healthy, available_gb=4.0, load_score=20.0)
    task = await _create_task(db_session, "programming", TaskStatus.PENDING.value)

    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 1
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.assigned_node_id == healthy.id
    assert refreshed.dispatch_hold_reason is None


async def test_specified_node_queues_when_not_eligible(
    db_session: AsyncSession,
) -> None:
    """UT-14 守卫：指定节点不达标 → 排队等它，**不改派**其他健康节点。"""
    blocked_target = await _create_agent_node(
        db_session, "programming", NodeStatus.IDLE.value
    )
    await _set_metrics(db_session, blocked_target, available_gb=0.7, load_score=25.0)
    healthy_other = await _create_agent_node(
        db_session, "programming", NodeStatus.IDLE.value
    )
    await _set_metrics(db_session, healthy_other, available_gb=6.0, load_score=5.0)
    task = await _create_task(db_session, "programming", TaskStatus.PENDING.value)
    task.target_node_id = blocked_target.id
    await db_session.commit()

    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 0
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.assigned_node_id is None  # 未被改派到 healthy_other
    assert refreshed.dispatch_hold_reason is not None
    assert "指定节点" in refreshed.dispatch_hold_reason


async def test_specified_node_dispatches_when_eligible(
    db_session: AsyncSession,
) -> None:
    """UT-13/UT-15 守卫：指定节点达标 → 当轮直达，不经 AI、不改选他台。"""
    target = await _create_agent_node(db_session, "programming", NodeStatus.IDLE.value, load=9.0)
    await _set_metrics(db_session, target, available_gb=3.0, load_score=15.0)
    lighter_other = await _create_agent_node(
        db_session, "programming", NodeStatus.IDLE.value, load=0.0
    )
    await _set_metrics(db_session, lighter_other, available_gb=8.0, load_score=1.0)
    task = await _create_task(db_session, "programming", TaskStatus.PENDING.value)
    task.target_node_id = target.id
    await db_session.commit()

    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 1
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    # 关键是「按指定」而非「按负载最优」
    assert refreshed.assigned_node_id == target.id


async def test_specified_node_forced_dispatch_bypasses_gate(
    db_session: AsyncSession,
) -> None:
    """UT-17：唯一例外——用户明确要求强行派送时越过门槛，且 forced_dispatch 为真。"""
    target = await _create_agent_node(db_session, "programming", NodeStatus.IDLE.value)
    await _set_metrics(db_session, target, available_gb=0.3, load_score=95.0)
    task = await _create_task(db_session, "programming", TaskStatus.PENDING.value)
    task.target_node_id = target.id
    task.forced_dispatch = True
    await db_session.commit()

    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 1
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.assigned_node_id == target.id
    assert refreshed.forced_dispatch is True  # 审计留痕
    assert refreshed.dispatch_hold_reason is None


async def test_unspecified_task_never_forced_to_unhealthy_node(
    db_session: AsyncSession,
) -> None:
    """UT-18 守卫：未指定节点时，即使存在不达标节点也**不派送**（偏好不得绕过门槛）。"""
    unhealthy = await _create_agent_node(db_session, "programming", NodeStatus.IDLE.value)
    await _set_metrics(db_session, unhealthy, available_gb=0.4, load_score=92.0)
    task = await _create_task(db_session, "programming", TaskStatus.PENDING.value)

    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 0
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.assigned_node_id is None
    assert refreshed.forced_dispatch is False


async def test_specified_node_offline_holds_with_offline_reason(
    db_session: AsyncSession,
) -> None:
    """指定节点离线 → 保持排队并说明「离线」，不派往其他节点、不静默。"""
    offline_target = await _create_agent_node(
        db_session, "programming", NodeStatus.OFFLINE.value
    )
    healthy_other = await _create_agent_node(
        db_session, "programming", NodeStatus.IDLE.value
    )
    await _set_metrics(db_session, healthy_other, available_gb=6.0, load_score=5.0)
    task = await _create_task(db_session, "programming", TaskStatus.PENDING.value)
    task.target_node_id = offline_target.id
    await db_session.commit()

    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 0
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.assigned_node_id is None
    assert refreshed.dispatch_hold_reason is not None
    assert "离线" in refreshed.dispatch_hold_reason


async def test_forced_dispatch_still_requires_node_online(
    db_session: AsyncSession,
) -> None:
    """强行派送也不能把任务塞给离线节点（离线派发只会白等超时）。"""
    offline_target = await _create_agent_node(
        db_session, "programming", NodeStatus.OFFLINE.value
    )
    task = await _create_task(db_session, "programming", TaskStatus.PENDING.value)
    task.target_node_id = offline_target.id
    task.forced_dispatch = True
    await db_session.commit()

    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 0
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.assigned_node_id is None
    assert refreshed.dispatch_hold_reason is not None
