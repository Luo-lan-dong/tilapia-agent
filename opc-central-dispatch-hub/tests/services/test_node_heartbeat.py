"""Agent 节点心跳监控服务测试。"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_node import AgentNode, NodeStatus
from app.models.system_settings import SystemSettings
from app.models.task import Task, TaskStatus
from app.services.node_heartbeat import (
    HEARTBEAT_TIMEOUT_SECONDS,
    mark_offline_nodes,
    reset_tasks_for_offline_nodes,
)


async def test_mark_offline_nodes_with_timeout(db_session: AsyncSession) -> None:
    """超过心跳超时的在线节点应被标记为 offline。"""
    node = AgentNode(
        name="test-agent-timeout",
        agent_type="programming",
        status=NodeStatus.IDLE.value,
        registration_token="token-" + "a" * 56,
        last_heartbeat_at=datetime.now(
            timezone.utc,  # noqa: UP017
        )
        - timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS + 1),
    )
    db_session.add(node)
    await db_session.commit()
    await db_session.refresh(node)

    offline_ids = await mark_offline_nodes(db_session)

    assert node.id in offline_ids
    refreshed_node = await db_session.get(AgentNode, node.id)
    assert refreshed_node is not None
    assert refreshed_node.status == NodeStatus.OFFLINE.value


async def test_mark_offline_nodes_with_null_heartbeat(db_session: AsyncSession) -> None:
    """从未上报心跳的在线节点应被标记为 offline。"""
    node = AgentNode(
        name="test-agent-no-heartbeat",
        agent_type="programming",
        status=NodeStatus.IDLE.value,
        registration_token="token-" + "b" * 56,
        last_heartbeat_at=None,
    )
    db_session.add(node)
    await db_session.commit()
    await db_session.refresh(node)

    offline_ids = await mark_offline_nodes(db_session)

    assert node.id in offline_ids
    refreshed_node = await db_session.get(AgentNode, node.id)
    assert refreshed_node is not None
    assert refreshed_node.status == NodeStatus.OFFLINE.value


async def test_mark_offline_nodes_recent_heartbeat(db_session: AsyncSession) -> None:
    """最近心跳过的节点不应被标记为 offline。"""
    node = AgentNode(
        name="test-agent-recent",
        agent_type="programming",
        status=NodeStatus.BUSY.value,
        registration_token="token-" + "c" * 56,
        last_heartbeat_at=datetime.now(
            timezone.utc,  # noqa: UP017
        )
        - timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS - 5),
    )
    db_session.add(node)
    await db_session.commit()
    await db_session.refresh(node)

    offline_ids = await mark_offline_nodes(db_session)

    assert node.id not in offline_ids
    refreshed_node = await db_session.get(AgentNode, node.id)
    assert refreshed_node is not None
    assert refreshed_node.status == NodeStatus.BUSY.value


async def test_mark_offline_nodes_already_offline(db_session: AsyncSession) -> None:
    """已是 offline 的节点不应被重复处理。"""
    node = AgentNode(
        name="test-agent-already-offline",
        agent_type="programming",
        status=NodeStatus.OFFLINE.value,
        registration_token="token-" + "d" * 56,
        last_heartbeat_at=None,
    )
    db_session.add(node)
    await db_session.commit()
    await db_session.refresh(node)

    offline_ids = await mark_offline_nodes(db_session)

    assert node.id not in offline_ids


async def test_reset_tasks_for_offline_nodes(db_session: AsyncSession) -> None:
    """离线节点负责的 assigned 和 running 任务应被重置为 pending。"""
    offline_node = AgentNode(
        name="test-agent-offline-tasks",
        agent_type="programming",
        status=NodeStatus.OFFLINE.value,
        registration_token="token-" + "e" * 56,
    )
    db_session.add(offline_node)
    await db_session.commit()
    await db_session.refresh(offline_node)

    assigned_task = Task(
        title="assigned task",
        agent_type="programming",
        status=TaskStatus.ASSIGNED.value,
        assigned_node_id=offline_node.id,
        progress=50,
    )
    running_task = Task(
        title="running task",
        agent_type="programming",
        status=TaskStatus.RUNNING.value,
        assigned_node_id=offline_node.id,
        progress=75,
    )
    completed_task = Task(
        title="completed task",
        agent_type="programming",
        status=TaskStatus.COMPLETED.value,
        assigned_node_id=offline_node.id,
        progress=100,
    )
    db_session.add_all([assigned_task, running_task, completed_task])
    await db_session.commit()

    reset_count = await reset_tasks_for_offline_nodes(
        db_session,
        [offline_node.id],
    )

    assert reset_count == 2

    refreshed_assigned = await db_session.get(Task, assigned_task.id)
    refreshed_running = await db_session.get(Task, running_task.id)
    refreshed_completed = await db_session.get(Task, completed_task.id)

    assert refreshed_assigned is not None
    assert refreshed_assigned.status == TaskStatus.PENDING.value
    assert refreshed_assigned.assigned_node_id is None
    assert refreshed_assigned.progress == 0

    assert refreshed_running is not None
    assert refreshed_running.status == TaskStatus.PENDING.value
    assert refreshed_running.assigned_node_id is None
    assert refreshed_running.progress == 0

    assert refreshed_completed is not None
    assert refreshed_completed.status == TaskStatus.COMPLETED.value
    assert refreshed_completed.assigned_node_id == offline_node.id


async def test_reset_tasks_for_offline_nodes_empty_list(db_session: AsyncSession) -> None:
    """传入空节点列表时不应重置任何任务。"""
    reset_count = await reset_tasks_for_offline_nodes(db_session, [])
    assert reset_count == 0


# ---------------- ADR-059 D5：AI 开关决定「自动改派」还是「先询问」----------------


async def _set_ai_dispatch_enabled(session: AsyncSession, enabled: bool) -> None:
    """设置 AI 智能调度开关（system_settings 单行）。"""
    result = await session.execute(
        select(SystemSettings).order_by(SystemSettings.created_at.asc()).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        session.add(SystemSettings(ai_dispatch_enabled=enabled))
    else:
        row.ai_dispatch_enabled = enabled
    await session.commit()


async def _offline_node_with_task(
    session: AsyncSession,
    *,
    status: str = TaskStatus.RUNNING.value,
) -> tuple[AgentNode, Task]:
    """构造「离线节点 + 该节点在跑任务」的场景。"""
    node = AgentNode(
        name=f"offline-{uuid.uuid4().hex[:8]}",
        agent_type="programming",
        status=NodeStatus.OFFLINE.value,
        # registration_token 列宽 64：只用 hex 前缀，避免超长截断报错
        registration_token=f"token-{uuid.uuid4().hex}",
    )
    session.add(node)
    await session.commit()
    await session.refresh(node)

    task = Task(
        title="offline-task",
        agent_type="programming",
        status=status,
        assigned_node_id=node.id,
        progress=40,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return node, task


async def test_offline_requeues_as_pending_when_ai_dispatch_enabled(
    db_session: AsyncSession,
) -> None:
    """AI 智能调度**开启** → 先改派：任务回 pending，调度器下一轮自动派发。"""
    await _set_ai_dispatch_enabled(db_session, True)
    node, task = await _offline_node_with_task(db_session)

    reset_count = await reset_tasks_for_offline_nodes(db_session, [node.id])

    assert reset_count == 1
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.PENDING.value
    assert refreshed.assigned_node_id is None
    assert refreshed.dispatch_hold_reason is not None
    assert "自动改派" in refreshed.dispatch_hold_reason


async def test_offline_requeues_as_requeue_pending_when_ai_dispatch_disabled(
    db_session: AsyncSession,
) -> None:
    """AI 智能调度**关闭** → 先询问：任务停在 requeue_pending，不得自动派发。"""
    await _set_ai_dispatch_enabled(db_session, False)
    node, task = await _offline_node_with_task(db_session)

    reset_count = await reset_tasks_for_offline_nodes(db_session, [node.id])

    assert reset_count == 1
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.REQUEUE_PENDING.value
    assert refreshed.assigned_node_id is None
    assert refreshed.progress == 0
    # 询问文案必须对用户可见（前端直接展示 dispatch_hold_reason）
    assert refreshed.dispatch_hold_reason is not None
    assert "确认改派" in refreshed.dispatch_hold_reason


async def test_requeue_pending_task_is_not_dispatched(
    db_session: AsyncSession,
) -> None:
    """核心守卫：`requeue_pending` 任务**不进入**调度器扫描结果（防未确认就自动跑）。"""
    from app.services.scheduler import dispatch_pending_tasks

    await _set_ai_dispatch_enabled(db_session, False)
    node, task = await _offline_node_with_task(db_session)
    await reset_tasks_for_offline_nodes(db_session, [node.id])

    # 另备一台健康节点：若状态过滤失效，任务会被派出去
    healthy = AgentNode(
        name=f"healthy-{uuid.uuid4().hex[:8]}",
        agent_type="programming",
        status=NodeStatus.IDLE.value,
        registration_token=f"token-{uuid.uuid4().hex}",
    )
    db_session.add(healthy)
    await db_session.commit()

    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 0
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.REQUEUE_PENDING.value
    assert refreshed.assigned_node_id is None


async def test_confirm_redispatch_moves_task_back_to_queue(
    db_session: AsyncSession,
) -> None:
    """用户确认后任务回 pending，可被调度器派发。"""
    from app.services.task_service import TaskService

    await _set_ai_dispatch_enabled(db_session, False)
    node, task = await _offline_node_with_task(db_session)
    await reset_tasks_for_offline_nodes(db_session, [node.id])

    response = await TaskService().confirm_redispatch(db_session, task.id)
    refreshed = await db_session.get(Task, task.id)

    assert response.status == TaskStatus.PENDING.value
    assert refreshed is not None
    assert refreshed.status == TaskStatus.PENDING.value
    assert refreshed.dispatch_hold_reason is None


async def test_confirm_redispatch_rejects_wrong_status(
    db_session: AsyncSession,
) -> None:
    """幂等保护：非 requeue_pending 状态调用确认 → 409，且状态不变。"""
    from app.core.errors import TaskDispatchError
    from app.services.task_service import TaskService

    node, task = await _offline_node_with_task(db_session)  # 仍是 running

    with pytest.raises(TaskDispatchError) as excinfo:
        await TaskService().confirm_redispatch(db_session, task.id)

    assert "requeue_pending" in excinfo.value.reason
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.RUNNING.value


async def test_requeue_pending_task_can_be_cancelled(
    db_session: AsyncSession,
) -> None:
    """待确认改派任务可被用户取消（属非终态）。"""
    from app.services.task_service import TaskService

    await _set_ai_dispatch_enabled(db_session, False)
    node, task = await _offline_node_with_task(db_session)
    await reset_tasks_for_offline_nodes(db_session, [node.id])

    result = await TaskService().cancel_task(db_session, task.id)

    assert result["status"] == TaskStatus.FAILED.value


async def test_requeue_pending_task_can_set_dispatch_preference(
    db_session: AsyncSession,
) -> None:
    """待确认改派任务可被直接指定节点改派（跳过确认流程）。"""
    from app.services.task_service import TaskService

    await _set_ai_dispatch_enabled(db_session, False)
    node, task = await _offline_node_with_task(db_session)
    await reset_tasks_for_offline_nodes(db_session, [node.id])

    response = await TaskService().set_dispatch_preference(
        db_session, task.id, target_node_id=node.id, force=False
    )

    assert response.status == TaskStatus.PENDING.value
    assert response.target_node_id == node.id


async def test_reset_tasks_for_offline_nodes_other_nodes_untouched(
    db_session: AsyncSession,
) -> None:
    """只重置指定离线节点的任务，不影响其他节点任务。"""
    offline_node = AgentNode(
        name="test-agent-offline-only",
        agent_type="programming",
        status=NodeStatus.OFFLINE.value,
        registration_token="token-" + "f" * 56,
    )
    other_node = AgentNode(
        name="test-agent-other",
        agent_type="programming",
        status=NodeStatus.BUSY.value,
        registration_token="token-" + "g" * 56,
    )
    db_session.add_all([offline_node, other_node])
    await db_session.commit()
    await db_session.refresh(offline_node)
    await db_session.refresh(other_node)

    other_task = Task(
        title="other node task",
        agent_type="programming",
        status=TaskStatus.RUNNING.value,
        assigned_node_id=other_node.id,
        progress=80,
    )
    db_session.add(other_task)
    await db_session.commit()

    reset_count = await reset_tasks_for_offline_nodes(
        db_session,
        [offline_node.id],
    )

    assert reset_count == 0
    refreshed_other = await db_session.get(Task, other_task.id)
    assert refreshed_other is not None
    assert refreshed_other.status == TaskStatus.RUNNING.value
    assert refreshed_other.assigned_node_id == other_node.id
    assert refreshed_other.progress == 80
