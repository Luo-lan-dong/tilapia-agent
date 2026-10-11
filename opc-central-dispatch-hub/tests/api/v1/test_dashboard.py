"""集群仪表盘聚合端点测试（ADR-038）。

覆盖 metrics / alerts / deliverables 三端点的核心场景：
- metrics：8 字段齐全；CPU 均值与排队数正确；无快照时 cluster_load=null
- alerts：离线节点=danger、failed/review=warning、排队>10min pending=info
- deliverables：缓存产物聚合；file_type 映射正确；按 mtime 倒序
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import create_app
from app.models.agent_node import AgentNode, NodeStatus
from app.models.llm_usage_record import LlmUsageRecord
from app.models.task import Task, TaskStatus
from app.services.dashboard_service import DashboardService


def _noop_start_background_task() -> tuple[asyncio.Event, asyncio.Task[None]]:
    """占位后台任务，避免真实调度器/心跳任务占用事件循环。"""
    stop_event = asyncio.Event()
    return stop_event, asyncio.create_task(_wait_forever(stop_event))


def _noop_start_reverse_notify() -> asyncio.Task[None]:
    """占位中枢重启反连任务。"""
    return asyncio.create_task(asyncio.sleep(0))


async def _wait_forever(stop_event: asyncio.Event) -> None:
    await stop_event.wait()


@pytest_asyncio.fixture
async def http_client(db_session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """创建 HTTP 异步测试客户端，注入测试会话。"""
    from app.api.deps import get_async_session

    async def _override_get_async_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    with (
        patch("app.main.start_scheduler", _noop_start_background_task),
        patch("app.main.start_heartbeat_monitor", _noop_start_background_task),
        patch("app.main.start_reverse_notify", _noop_start_reverse_notify),
    ):
        app = create_app()
        app.dependency_overrides[get_async_session] = _override_get_async_session
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


def _make_node(
    *,
    name: str = "节点A",
    agent_type: str = "code",
    status: str = NodeStatus.IDLE.value,
    metrics: dict | None = None,
) -> AgentNode:
    return AgentNode(
        name=name,
        agent_type=agent_type,
        capabilities=["pytest"],
        status=status,
        load=0.0,
        registration_token=f"token-{uuid.uuid4()}",
        metrics_snapshot=metrics,
    )


def _make_task(
    *,
    title: str = "任务",
    agent_type: str = "code",
    status: str = TaskStatus.PENDING.value,
    progress: int = 0,
    created_at: datetime | None = None,
    plan_content: str | None = None,
) -> Task:
    return Task(
        title=title,
        agent_type=agent_type,
        status=status,
        progress=progress,
        specification={"spec": "x", "tasks": "x", "checklist": "x"},
        source="spec_chat",
        execution_mode="plan" if plan_content is not None else "build",
        execution_target="code",
        plan_content=plan_content,
        created_at=created_at or datetime.now(UTC),
    )


async def test_metrics_aggregates_eight_fields(db_session: AsyncSession) -> None:
    """metrics 聚合：任务计数、在线节点、CPU 均值、告警数、token、排队数。"""
    node_online = _make_node(
        name="在线节点", metrics={"cpu": {"percent": 60.0}, "memory": {"percent": 40.0}}
    )
    node_offline = _make_node(name="离线节点", status=NodeStatus.OFFLINE.value)
    db_session.add_all([node_online, node_offline])

    t_pending = _make_task(title="排队", status=TaskStatus.PENDING.value)
    t_running = _make_task(title="执行中", status=TaskStatus.RUNNING.value, progress=50)
    t_completed = _make_task(title="完成", status=TaskStatus.COMPLETED.value, progress=100)
    db_session.add_all([t_pending, t_running, t_completed])

    usage = LlmUsageRecord(
        scene="planning", model="m", prompt_tokens=100, completion_tokens=50, total_tokens=150
    )
    db_session.add(usage)
    await db_session.commit()

    service = DashboardService()
    data = await service.get_metrics(db_session)

    assert data.total_tasks == 3
    assert data.running_tasks == 1
    assert data.completed_tasks == 1
    assert data.queued_tasks == 1
    assert data.online_nodes == 1
    assert data.total_nodes == 2
    assert data.cluster_load_percent == 60.0
    assert data.token_consumed_today == 150
    # 1 个离线节点 → danger 告警
    assert data.alert_count == 1


async def test_metrics_no_metrics_snapshot_returns_null_load(db_session: AsyncSession) -> None:
    """在线节点无 metrics 快照：cluster_load_percent 为 None。"""
    db_session.add(_make_node(name="无快照节点"))
    await db_session.commit()

    service = DashboardService()
    data = await service.get_metrics(db_session)

    assert data.cluster_load_percent is None
    assert data.total_nodes == 1
    assert data.online_nodes == 1


async def test_alerts_danger_warning_info_levels(db_session: AsyncSession) -> None:
    """告警三态：离线=danger、failed/review=warning、排队>10min pending=info。"""
    node_offline = _make_node(name="离线", status=NodeStatus.OFFLINE.value)
    db_session.add(node_offline)

    t_failed = _make_task(title="失败任务", status=TaskStatus.FAILED.value, progress=30)
    t_review = _make_task(
        title="待确认任务",
        status=TaskStatus.REVIEW.value,
        progress=100,
        plan_content="# 方案",
    )
    old_time = datetime.now(UTC) - timedelta(minutes=20)
    t_pending_old = _make_task(
        title="排队超时", status=TaskStatus.PENDING.value, created_at=old_time
    )
    db_session.add_all([t_failed, t_review, t_pending_old])
    await db_session.commit()

    service = DashboardService()
    data = await service.get_alerts(db_session, limit=50)

    levels = {item.level for item in data.items}
    assert levels == {"danger", "warning", "info"}
    assert data.total == 4  # 1 danger + 2 warning + 1 info

    danger = next(i for i in data.items if i.level == "danger")
    assert danger.node_id == str(node_offline.id)

    warning_items = [i for i in data.items if i.level == "warning"]
    assert len(warning_items) == 2

    info_items = [i for i in data.items if i.level == "info"]
    assert len(info_items) == 1
    assert info_items[0].task_id == str(t_pending_old.id)


async def test_deliverables_aggregates_cached_trees_by_mtime(db_session: AsyncSession) -> None:
    """交付物聚合：读 artifact_service 缓存，按 mtime 倒序，file_type 正确映射。"""
    node = _make_node(name="交付节点")
    db_session.add(node)
    await db_session.flush()

    t1 = _make_task(title="较新任务", status=TaskStatus.COMPLETED.value, progress=100)
    t2 = _make_task(title="较旧任务", status=TaskStatus.COMPLETED.value, progress=100)
    t1.assigned_node_id = node.id
    t2.assigned_node_id = node.id
    db_session.add_all([t1, t2])
    await db_session.commit()
    await db_session.refresh(t1)
    await db_session.refresh(t2)

    fake_trees: dict[str, dict] = {
        str(t1.id): {
            "updated_at": 2.0,
            "files": [
                {"path": "assets/kv.png", "size": 1024, "mtime": 200.0},
                {"path": "src/main.py", "size": 500, "mtime": 100.0},
            ],
        },
        str(t2.id): {
            "updated_at": 1.0,
            "files": [{"path": "out/demo.mp4", "size": 50000, "mtime": 150.0}],
        },
    }

    with patch(
        "app.services.dashboard_service.artifact_service.get_tree",
        side_effect=lambda tid: fake_trees.get(tid),
    ):
        service = DashboardService()
        data = await service.get_deliverables(db_session, limit=20)

    assert data.total == 3
    # mtime 倒序：200 → 150 → 100
    assert data.items[0].file_name == "kv.png"
    assert data.items[0].file_type == "image"
    assert data.items[1].file_name == "demo.mp4"
    assert data.items[1].file_type == "video"
    assert data.items[2].file_name == "main.py"
    assert data.items[2].file_type == "code"
    assert data.items[0].node_name == "交付节点"


async def test_deliverables_empty_when_no_cache(db_session: AsyncSession) -> None:
    """无产物缓存：交付物列表为空。"""
    t = _make_task(title="空任务", status=TaskStatus.COMPLETED.value, progress=100)
    db_session.add(t)
    await db_session.commit()

    with patch(
        "app.services.dashboard_service.artifact_service.get_tree",
        return_value=None,
    ):
        service = DashboardService()
        data = await service.get_deliverables(db_session)

    assert data.total == 0
    assert data.items == []


async def test_mark_all_alerts_read_hides_then_reappears(db_session: AsyncSession) -> None:
    """一键已读（ADR-038）：水位线后的告警隐藏，事件时间更新后重新出现。"""
    node = _make_node(name="离线节点", status=NodeStatus.OFFLINE.value)
    db_session.add(node)
    await db_session.commit()

    service = DashboardService()
    data = await service.get_alerts(db_session)
    assert data.total == 1

    await service.mark_all_alerts_read(db_session)

    data = await service.get_alerts(db_session)
    assert data.total == 0
    assert data.items == []

    # 节点再次离线（心跳时间更新到水位线之后）→ 告警重新出现
    node.last_heartbeat_at = datetime.now(UTC) + timedelta(seconds=5)
    await db_session.commit()
    data = await service.get_alerts(db_session)
    assert data.total == 1


async def test_mark_all_read_endpoint_returns_watermark(http_client: AsyncClient) -> None:
    """HTTP 端点：POST /dashboard/alerts/read-all 返回水位线并清空未读。"""
    response = await http_client.post("/api/v1/dashboard/alerts/read-all")
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["read_at"]

    listing = await http_client.get("/api/v1/dashboard/alerts")
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] == 0


async def test_metrics_endpoint_returns_200(http_client: AsyncClient) -> None:
    """HTTP 端点：GET /dashboard/metrics 返回 8 字段。"""
    response = await http_client.get("/api/v1/dashboard/metrics")
    assert response.status_code == 200
    data = response.json()["data"]
    for field in (
        "total_tasks",
        "running_tasks",
        "completed_tasks",
        "queued_tasks",
        "online_nodes",
        "total_nodes",
        "cluster_load_percent",
        "alert_count",
        "token_consumed_today",
    ):
        assert field in data


async def test_alerts_endpoint_returns_200(http_client: AsyncClient) -> None:
    """HTTP 端点：GET /dashboard/alerts 返回告警列表。"""
    response = await http_client.get("/api/v1/dashboard/alerts")
    assert response.status_code == 200
    data = response.json()["data"]
    assert "total" in data
    assert "items" in data


async def test_deliverables_endpoint_returns_200(http_client: AsyncClient) -> None:
    """HTTP 端点：GET /dashboard/deliverables 返回交付物列表。"""
    response = await http_client.get("/api/v1/dashboard/deliverables")
    assert response.status_code == 200
    data = response.json()["data"]
    assert "total" in data
    assert "items" in data
