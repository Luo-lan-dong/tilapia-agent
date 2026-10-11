"""派发偏好测试（ADR-059 D2/D5）：指定节点排队与失败任务改派。

覆盖 `TaskService.set_dispatch_preference` 与 `dispatch_task(force=...)` 的关键语义：

- 指定节点 → 任务回 pending 只等该节点（不经 AI）；
- **failed 任务可改派**且复用既有文档（不重生成三件套）——用户明确要求的能力；
- completed 拒绝改派（应走迭代链）；
- force=true 才允许越过准入门槛，并落 `forced_dispatch` 审计。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import TaskDispatchError
from app.models.agent_node import AgentNode, NodeStatus
from app.models.dispatch_decision import DispatchDecision
from app.models.task import Task, TaskStatus
from app.models.task_building_session import TaskBuildingSession
from app.services.task_service import TaskService


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """清理任务与节点表，避免历史数据干扰。"""
    await db_session.execute(delete(DispatchDecision))
    await db_session.execute(TaskBuildingSession.__table__.update().values(task_id=None))
    await db_session.execute(delete(TaskBuildingSession))
    await db_session.execute(delete(Task))
    await db_session.execute(delete(AgentNode))
    yield


def _unique_token() -> str:
    return f"token-{uuid.uuid4().hex}"


async def _create_node(
    session: AsyncSession,
    *,
    available_gb: float = 4.0,
    load_score: float = 10.0,
) -> AgentNode:
    """创建带 metrics 的在线节点（准入判据来源）。"""
    node = AgentNode(
        name=f"node-{uuid.uuid4().hex[:8]}",
        agent_type="code",
        status=NodeStatus.IDLE.value,
        load=0.0,
        registration_token=_unique_token(),
        metrics_snapshot={
            "memory": {"percent": 40.0, "available_gb": available_gb},
            "load_score": {"score": load_score},
            "cpu": {"percent": 10.0},
        },
    )
    session.add(node)
    await session.commit()
    await session.refresh(node)
    return node


async def _create_task(
    session: AsyncSession,
    *,
    status: str = TaskStatus.PENDING.value,
    with_spec: bool = True,
) -> Task:
    task = Task(
        title=f"task-{uuid.uuid4().hex[:8]}",
        agent_type="code",
        status=status,
        specification=(
            {"spec": "# 原有方案", "tasks": "# 原有任务", "checklist": "# 原有清单"}
            if with_spec
            else None
        ),
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


async def test_set_preference_targets_node_and_requeues(
    db_session: AsyncSession,
) -> None:
    """指定节点 → 任务回 pending 等待该节点，并清除排队原因。"""
    node = await _create_node(db_session)
    task = await _create_task(db_session)
    task.dispatch_hold_reason = "旧排队原因"
    await db_session.commit()

    response = await TaskService().set_dispatch_preference(
        db_session, task.id, target_node_id=node.id, force=False
    )

    assert response.target_node_id == node.id
    assert response.target_node_name == node.name
    assert response.status == TaskStatus.PENDING.value
    assert response.dispatch_hold_reason is None

    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.target_node_id == node.id
    assert refreshed.forced_dispatch is False


async def test_failed_task_can_be_redispatched_without_regenerating_docs(
    db_session: AsyncSession,
) -> None:
    """**失败任务可改派**：回 pending + 恢复 auto 与窗口，且原三件套原样保留。

    这是用户明确要求的语义——改派复用既有文档与产物，不重新生成三件套。
    """
    node = await _create_node(db_session)
    task = await _create_task(db_session, status=TaskStatus.FAILED.value)
    task.progress = 60
    task.dispatch_type = "manual"
    task.dispatch_window_until = None
    task.change_summary = "上次失败原因"
    original_spec = dict(task.specification)
    await db_session.commit()

    response = await TaskService().set_dispatch_preference(
        db_session, task.id, target_node_id=node.id, force=False
    )

    assert response.status == TaskStatus.PENDING.value
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.PENDING.value
    assert refreshed.assigned_node_id is None
    assert refreshed.progress == 0
    assert refreshed.dispatch_type == "auto"  # 交还调度器，不卡在 pending+manual
    assert refreshed.dispatch_window_until is not None
    # 不重生成三件套：原文档逐字保留；上轮失败说明保留供追溯
    assert refreshed.specification == original_spec
    assert refreshed.change_summary == "上次失败原因"
    assert refreshed.target_node_id == node.id


async def test_completed_task_rejects_preference_change(
    db_session: AsyncSession,
) -> None:
    """已完成任务拒绝改派（应走任务链迭代）。"""
    node = await _create_node(db_session)
    task = await _create_task(db_session, status=TaskStatus.COMPLETED.value)

    with pytest.raises(TaskDispatchError) as excinfo:
        await TaskService().set_dispatch_preference(
            db_session, task.id, target_node_id=node.id, force=False
        )

    assert "已完成" in excinfo.value.reason


async def test_running_task_rejects_preference_change(
    db_session: AsyncSession,
) -> None:
    """执行中任务拒绝改派（需先取消或等终态），避免与在跑节点状态冲突。"""
    node = await _create_node(db_session)
    task = await _create_task(db_session, status=TaskStatus.RUNNING.value)

    with pytest.raises(TaskDispatchError) as excinfo:
        await TaskService().set_dispatch_preference(
            db_session, task.id, target_node_id=node.id, force=False
        )

    assert "正在执行" in excinfo.value.reason


async def test_force_flag_is_recorded_for_audit(db_session: AsyncSession) -> None:
    """force=true 落 forced_dispatch（唯一越门槛路径必须可追溯）。"""
    node = await _create_node(db_session)
    task = await _create_task(db_session)

    response = await TaskService().set_dispatch_preference(
        db_session, task.id, target_node_id=node.id, force=True
    )

    assert response.forced_dispatch is True
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.forced_dispatch is True


async def test_clearing_target_returns_to_regular_prefilter(
    db_session: AsyncSession,
) -> None:
    """传 target_node_id=None 清除指定，回到常规预筛（并清 forced 标记）。"""
    node = await _create_node(db_session)
    task = await _create_task(db_session)
    task.target_node_id = node.id
    task.forced_dispatch = True
    await db_session.commit()

    response = await TaskService().set_dispatch_preference(
        db_session, task.id, target_node_id=None, force=False
    )

    assert response.target_node_id is None
    assert response.forced_dispatch is False


async def test_preference_clears_rejection_backoff(db_session: AsyncSession) -> None:
    """用户显式指定即覆盖上轮拒单：清除回避记录，指定节点立即可被考虑。"""
    from app.services import dispatch_backoff

    dispatch_backoff.reset_all()
    node = await _create_node(db_session)
    task = await _create_task(db_session)
    dispatch_backoff.record_rejection(str(task.id), str(node.id))
    assert str(node.id) in dispatch_backoff.excluded_nodes(str(task.id))

    await TaskService().set_dispatch_preference(
        db_session, task.id, target_node_id=node.id, force=False
    )

    assert str(node.id) not in dispatch_backoff.excluded_nodes(str(task.id))
    dispatch_backoff.reset_all()
