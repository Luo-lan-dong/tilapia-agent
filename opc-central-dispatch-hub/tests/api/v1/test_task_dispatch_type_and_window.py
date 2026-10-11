"""任务派发模式（手动 / 自动）+ 分配节点显示名称 测试（ADR-017）。"""

import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AgentNotFoundError, TaskDispatchError, TaskNotFoundError
from app.main import create_app
from app.models.agent_node import AgentNode, NodeStatus
from app.models.task import Task, TaskStatus
from app.services.task_service import TaskService

pytestmark = pytest.mark.asyncio


# -------------------------- Service 单元 / 集成 -------------------------- #


async def _create_node(
    db_session: AsyncSession,
    *,
    name: str = "node-1",
    status: str = NodeStatus.IDLE.value,
) -> AgentNode:
    node = AgentNode(
        name=name,
        agent_type="code",
        status=status,
        load=0.0,
        registration_token=f"token_{uuid.uuid4().hex[:8]}",
    )
    db_session.add(node)
    await db_session.commit()
    await db_session.refresh(node)
    return node


async def _create_task(
    db_session: AsyncSession,
    *,
    title: str = "t1",
    status: str = TaskStatus.PENDING.value,
    agent_type: str = "code",
    assigned_node_id: uuid.UUID | None = None,
    dispatch_type: str = "auto",
    dispatch_window_until: datetime | None = None,
    source: str = "manual",
    specification: dict | None = None,
    progress: int = 0,
) -> Task:
    spec = specification or {
        "spec": "# Spec",
        "tasks": "# Tasks",
        "checklist": "# Checklist",
    }
    task = Task(
        title=title,
        description="desc",
        agent_type=agent_type,
        status=status,
        assigned_node_id=assigned_node_id,
        progress=progress,
        source=source,
        specification=spec,
        dispatch_type=dispatch_type,
        dispatch_window_until=dispatch_window_until,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    return task


async def test_create_task_sets_dispatch_window_until(
    db_session: AsyncSession,
) -> None:
    """TaskService.create_task 落地 dispatch_window_until ≈ now + 配置。"""
    service = TaskService()
    from app.schemas.task import TaskCreateRequest
    from app.schemas.task_building import TaskSpecification

    request = TaskCreateRequest(
        requirement="做个登录页",
        specification=TaskSpecification(
            spec="# S", tasks="# T", checklist="# C", is_ready_to_create=True
        ),
    )
    before = datetime.now(UTC)
    response = await service.create_task(db_session, request)
    after = datetime.now(UTC)

    assert response.dispatch_type == "auto"
    assert response.dispatch_window_until is not None
    # 窗口时间 = now + 10s，容许 ±1s 抖动
    expected_min = before + timedelta(seconds=9)
    expected_max = after + timedelta(seconds=11)
    assert expected_min <= response.dispatch_window_until <= expected_max


async def test_create_task_default_dispatch_type_is_auto(
    db_session: AsyncSession,
) -> None:
    """新创建任务默认 dispatch_type='auto'。"""
    service = TaskService()
    from app.schemas.task import TaskCreateRequest
    from app.schemas.task_building import TaskSpecification

    request = TaskCreateRequest(
        requirement="做个搜索",
        specification=TaskSpecification(
            spec="# S", tasks="# T", checklist="# C", is_ready_to_create=True
        ),
    )
    response = await service.create_task(db_session, request)
    assert response.dispatch_type == "auto"


async def test_manual_dispatch_writes_manual_and_clears_window(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """手动 dispatch → dispatch_type='manual'、dispatch_window_until=NULL。"""
    task = await _create_task(db_session, source="manual")
    service = TaskService()

    with caplog.at_level(logging.INFO, logger="app.services.task_service"):
        result = await service.dispatch_task(db_session, task.id, target="code")

    assert result["dispatch_type"] == "manual"
    assert result["target"] == "code"

    row = await db_session.execute(select(Task).where(Task.id == task.id))
    fresh = row.scalar_one()
    assert fresh.dispatch_type == "manual"
    assert fresh.dispatch_window_until is None
    assert fresh.status == TaskStatus.PENDING.value
    assert fresh.assigned_node_id is None

    assert any(rec.message == "task_manual_dispatched" for rec in caplog.records), (
        "expected task_manual_dispatched log"
    )


async def test_manual_dispatch_allows_non_spec_chat_source(
    db_session: AsyncSession,
) -> None:
    """手动 dispatch 不限制 source：source='manual' 也能手动派发。"""
    task = await _create_task(db_session, source="manual")
    service = TaskService()
    # 不抛异常即通过
    result = await service.dispatch_task(db_session, task.id, target="code")
    assert result["dispatch_type"] == "manual"


async def test_manual_dispatch_rejects_running_task(
    db_session: AsyncSession,
) -> None:
    """running 状态拒绝手动 dispatch（沿用旧逻辑）。"""
    task = await _create_task(
        db_session,
        status=TaskStatus.RUNNING.value,
        source="manual",
    )
    service = TaskService()
    with pytest.raises(TaskDispatchError):
        await service.dispatch_task(db_session, task.id, target="code")


async def test_manual_dispatch_pending_manual_allowed(
    db_session: AsyncSession,
) -> None:
    """pending+manual（plan 批准后的等待态）允许再次手动派发（ADR-017 修正）。

    旧规则"已是 manual 一律拒绝"会把批准方案后的任务永久卡死在待分配：
    调度器不接管 manual，前端/后端又拒绝再次派发。
    """
    task = await _create_task(
        db_session,
        dispatch_type="manual",
        dispatch_window_until=None,
    )
    service = TaskService()

    result = await service.dispatch_task(db_session, task.id, target="code")

    assert result["dispatch_type"] == "manual"
    row = await db_session.execute(select(Task).where(Task.id == task.id))
    fresh = row.scalar_one()
    assert fresh.status == TaskStatus.PENDING.value
    assert fresh.dispatch_type == "manual"
    assert fresh.dispatch_window_until is None


async def test_manual_dispatch_rejects_assigned_task(
    db_session: AsyncSession,
) -> None:
    """assigned（已分配节点在途）状态拒绝手动 dispatch，防重复派发。"""
    node = await _create_node(db_session, name="占用节点")
    task = await _create_task(
        db_session,
        status=TaskStatus.ASSIGNED.value,
        assigned_node_id=node.id,
        dispatch_type="manual",
    )
    service = TaskService()
    with pytest.raises(TaskDispatchError, match="已分配节点"):
        await service.dispatch_task(db_session, task.id, target="code")


async def test_plan_completed_task_dispatch_after_approval(
    db_session: AsyncSession,
) -> None:
    """plan 任务完成（completed+manual）后批准方案 → 转 build 入队等待手动派发。

    模拟 approve_plan 链路：completed 状态不触发防重复拦截，execution_mode
    改 build 后经 dispatch_task 落为 pending+manual，可被用户手动派发。
    """
    task = await _create_task(
        db_session,
        status=TaskStatus.COMPLETED.value,
        dispatch_type="manual",
        dispatch_window_until=None,
    )
    task.execution_mode = "build"
    await db_session.commit()

    service = TaskService()
    result = await service.dispatch_task(db_session, task.id, target="code")

    assert result["status"] == "queued"
    row = await db_session.execute(select(Task).where(Task.id == task.id))
    fresh = row.scalar_one()
    assert fresh.status == TaskStatus.PENDING.value
    assert fresh.execution_mode == "build"
    assert fresh.dispatch_type == "manual"


async def test_manual_dispatch_task_not_found(db_session: AsyncSession) -> None:
    """任务不存在抛 TaskNotFoundError。"""
    service = TaskService()
    with pytest.raises(TaskNotFoundError):
        await service.dispatch_task(db_session, uuid.uuid4(), target="code")


async def test_manual_dispatch_with_node_id_assigns_task(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """dispatch 带 node_id → 任务直接 assigned 给该节点并立即下发（ADR-017 增强）。"""
    node = await _create_node(db_session, name="指定节点")
    task = await _create_task(db_session)
    service = TaskService()

    with (
        patch(
            "app.services.scheduler._send_dispatch_to_node",
            new_callable=AsyncMock,
        ) as mock_send,
        caplog.at_level(logging.INFO, logger="app.services.task_service"),
    ):
        result = await service.dispatch_task(db_session, task.id, target="code", node_id=node.id)

    assert result["status"] == "assigned"
    assert result["node_id"] == str(node.id)
    assert result["dispatch_type"] == "manual"

    row = await db_session.execute(select(Task).where(Task.id == task.id))
    fresh = row.scalar_one()
    assert fresh.status == TaskStatus.ASSIGNED.value
    assert fresh.assigned_node_id == node.id
    assert fresh.dispatch_type == "manual"
    assert fresh.dispatch_window_until is None

    mock_send.assert_awaited_once()
    assert any(rec.message == "task_manual_dispatched_to_node" for rec in caplog.records), (
        "expected task_manual_dispatched_to_node log"
    )


async def test_manual_dispatch_node_type_mismatch_rejected(
    db_session: AsyncSession,
) -> None:
    """节点 agent_type 与任务 agent_type 不一致时拒绝派发。"""
    node = await _create_node(db_session, name="code-node")  # agent_type='code'
    task = await _create_task(db_session, agent_type="design")
    service = TaskService()
    with pytest.raises(TaskDispatchError, match="节点类型不匹配"):
        await service.dispatch_task(db_session, task.id, target="code", node_id=node.id)


async def test_manual_dispatch_node_offline_rejected(
    db_session: AsyncSession,
) -> None:
    """目标节点离线时拒绝派发。"""
    node = await _create_node(db_session, name="离线节点", status=NodeStatus.OFFLINE.value)
    task = await _create_task(db_session)
    service = TaskService()
    with pytest.raises(TaskDispatchError, match="离线"):
        await service.dispatch_task(db_session, task.id, target="code", node_id=node.id)


async def test_manual_dispatch_node_not_found(db_session: AsyncSession) -> None:
    """指定的节点不存在抛 AgentNotFoundError。"""
    task = await _create_task(db_session)
    service = TaskService()
    with pytest.raises(AgentNotFoundError):
        await service.dispatch_task(db_session, task.id, target="code", node_id=uuid.uuid4())


async def test_failed_manual_task_redispatch_with_node(
    db_session: AsyncSession,
) -> None:
    """失败的手动派发任务允许选节点重新派发（ADR-017 补充）。"""
    node = await _create_node(db_session, name="重派节点")
    task = await _create_task(
        db_session,
        status=TaskStatus.FAILED.value,
        dispatch_type="manual",
        dispatch_window_until=None,
        assigned_node_id=node.id,
        progress=20,
    )
    service = TaskService()

    result = await service.dispatch_task(db_session, task.id, target="code", node_id=node.id)

    assert result["status"] == "assigned"
    row = await db_session.execute(select(Task).where(Task.id == task.id))
    fresh = row.scalar_one()
    assert fresh.status == TaskStatus.ASSIGNED.value
    assert fresh.assigned_node_id == node.id
    assert fresh.dispatch_type == "manual"
    assert fresh.dispatch_window_until is None
    assert fresh.progress == 0


async def test_failed_auto_task_redispatch_without_node_requeues(
    db_session: AsyncSession,
) -> None:
    """失败的 auto 任务不带节点重派 = 重新入队：pending + auto + 恢复派发窗口。"""
    task = await _create_task(
        db_session,
        status=TaskStatus.FAILED.value,
        dispatch_type="auto",
        dispatch_window_until=None,
        progress=30,
    )
    service = TaskService()
    before = datetime.now(UTC)

    result = await service.dispatch_task(db_session, task.id, target="code")

    # 无节点重派的响应契约是 "queued"（重新入队），落库状态为 pending
    assert result["status"] == "queued"
    row = await db_session.execute(select(Task).where(Task.id == task.id))
    fresh = row.scalar_one()
    assert fresh.status == TaskStatus.PENDING.value
    assert fresh.assigned_node_id is None
    assert fresh.dispatch_type == "auto"
    # 窗口恢复为 now + 配置秒数（容许 ±1s 抖动）
    assert fresh.dispatch_window_until is not None
    assert before + timedelta(seconds=9) <= fresh.dispatch_window_until
    assert fresh.progress == 0


async def test_list_tasks_returns_assigned_node_name(
    db_session: AsyncSession,
) -> None:
    """已分配节点的任务在列表里 assigned_node_name = agent_nodes.name。"""
    node = await _create_node(db_session, name="笔记本")
    await _create_task(
        db_session,
        title="task-with-node",
        assigned_node_id=node.id,
        status=TaskStatus.RUNNING.value,
    )
    service = TaskService()
    response = await service.list_tasks(
        db_session,
        status_filter=None,
        agent_type=None,
        limit=20,
        offset=0,
    )
    target = next(t for t in response.items if t.title == "task-with-node")
    assert target.assigned_node_id == node.id
    assert target.assigned_node_name == "笔记本"


async def test_list_tasks_returns_null_assigned_node_name(
    db_session: AsyncSession,
) -> None:
    """未分配节点的任务 assigned_node_name IS NULL（outerjoin）。"""
    await _create_task(
        db_session,
        title="task-without-node",
        assigned_node_id=None,
    )
    service = TaskService()
    response = await service.list_tasks(
        db_session,
        status_filter=None,
        agent_type=None,
        limit=20,
        offset=0,
    )
    target = next(t for t in response.items if t.title == "task-without-node")
    assert target.assigned_node_id is None
    assert target.assigned_node_name is None


async def test_list_tasks_returns_dispatch_type_and_window(
    db_session: AsyncSession,
) -> None:
    """任务列表响应包含 dispatch_type 与 dispatch_window_until。"""
    future = datetime.now(UTC) + timedelta(seconds=30)
    await _create_task(
        db_session,
        title="task-window",
        dispatch_type="auto",
        dispatch_window_until=future,
    )
    service = TaskService()
    response = await service.list_tasks(db_session, None, None, 20, 0)
    target = next(t for t in response.items if t.title == "task-window")
    assert target.dispatch_type == "auto"
    assert target.dispatch_window_until is not None


# -------------------------- Scheduler 单元 / 集成 -------------------------- #


async def test_scheduler_skips_manual_dispatch_type(
    db_session: AsyncSession,
) -> None:
    """dispatch_type='manual' 的 pending 任务不被调度器接管。"""
    from app.services.scheduler import dispatch_pending_tasks

    node = await _create_node(db_session, name="n1", status=NodeStatus.IDLE.value)
    # 1 条 manual（应跳过）+ 1 条 auto + window 已过（应被派发）
    await _create_task(
        db_session,
        title="manual-task",
        dispatch_type="manual",
        dispatch_window_until=None,
    )
    await _create_task(
        db_session,
        title="auto-task",
        dispatch_type="auto",
        dispatch_window_until=datetime.now(UTC) - timedelta(seconds=1),
    )

    count = await dispatch_pending_tasks(db_session)
    assert count == 1

    rows = await db_session.execute(select(Task).order_by(Task.title))
    tasks = rows.scalars().all()
    by_title = {t.title: t for t in tasks}
    assert by_title["manual-task"].status == TaskStatus.PENDING.value
    assert by_title["auto-task"].status == TaskStatus.ASSIGNED.value
    assert by_title["auto-task"].assigned_node_id == node.id


async def test_scheduler_skips_window_not_yet_expired(
    db_session: AsyncSession,
) -> None:
    """窗口未到期的 auto 任务被跳过；窗口已过的被派发。"""
    from app.services.scheduler import dispatch_pending_tasks

    node = await _create_node(db_session, name="n1", status=NodeStatus.IDLE.value)
    # 1 条窗口未到期（应跳过）+ 1 条窗口已过（应派发）
    await _create_task(
        db_session,
        title="fresh-task",
        dispatch_type="auto",
        dispatch_window_until=datetime.now(UTC) + timedelta(seconds=60),
    )
    await _create_task(
        db_session,
        title="expired-task",
        dispatch_type="auto",
        dispatch_window_until=datetime.now(UTC) - timedelta(seconds=1),
    )

    count = await dispatch_pending_tasks(db_session)
    assert count == 1

    rows = await db_session.execute(select(Task).order_by(Task.title))
    tasks = rows.scalars().all()
    by_title = {t.title: t for t in tasks}
    assert by_title["fresh-task"].status == TaskStatus.PENDING.value
    assert by_title["fresh-task"].assigned_node_id is None
    assert by_title["expired-task"].status == TaskStatus.ASSIGNED.value
    assert by_title["expired-task"].assigned_node_id == node.id


# -------------------------- HTTP 契约 -------------------------- #


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


def _noop(*_args, **_kwargs):  # pragma: no cover
    import asyncio

    return asyncio.Event(), None


async def test_list_tasks_response_includes_dispatch_type(
    http_client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /tasks 响应里 dispatch_type ∈ {'auto','manual'}。"""
    await _create_task(db_session, title="h-1", dispatch_type="auto")
    response = await http_client.get("/api/v1/tasks")
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    items = body["data"]["items"]
    assert items
    for item in items:
        assert item["dispatch_type"] in ("auto", "manual")


async def test_list_tasks_response_includes_assigned_node_name(
    http_client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /tasks 响应里 assigned_node_name 与 agent_nodes.name 一致。"""
    node = await _create_node(db_session, name="r410")
    await _create_task(
        db_session,
        title="h-2",
        assigned_node_id=node.id,
        status=TaskStatus.RUNNING.value,
    )
    response = await http_client.get("/api/v1/tasks")
    body = response.json()
    target = next(t for t in body["data"]["items"] if t["title"] == "h-2")
    assert target["assigned_node_name"] == "r410"


async def test_dispatch_task_response_includes_dispatch_type_manual(
    http_client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /tasks/{id}/dispatch 响应 data.dispatch_type == 'manual'。"""
    task = await _create_task(db_session, title="h-3")
    response = await http_client.post(
        f"/api/v1/tasks/{task.id}/dispatch",
        json={"target": "code"},
    )
    assert response.status_code == 200
    body = response.json()
    # 响应字段含 dispatch_type（response_model_exclude_unset 可能把 None 字段过滤）
    assert "dispatch_type" in body["data"], f"missing dispatch_type in {body}"
    assert body["data"]["dispatch_type"] == "manual"
    assert body["data"]["target"] == "code"
