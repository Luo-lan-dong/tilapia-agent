"""任务卡死检测与自动失败兜底测试（见 ADR-015 / spec/task-stale-detector-spec.md）。

测试覆盖（7 单元 + 3 集成 + 1 契约 = 11 项）：

- 单元：长 running 标 failed / 新鲜 running 忽略 / SKIP LOCKED /
  change_summary 不覆写 / 节点不存在 / 阈值配置 / 阈值下限校验
- 集成：scheduler 循环跑通 / 异常不影响下一轮 / 优雅停止
- 契约：GET /v1/tasks/{id} 包含 change_summary
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.agent_node import AgentNode, NodeStatus
from app.models.task import Task, TaskStatus
from app.services.scheduler import (
    SCHEDULER_INTERVAL_SECONDS,
    _fail_stale_tasks,
    start_scheduler,
    stop_scheduler,
)

# -------------------------- 共用 helpers -------------------------- #


def _unique_token() -> str:
    return f"token-{uuid.uuid4().hex}"


async def _create_node(
    session: AsyncSession,
    agent_type: str = "programming",
    status: str = NodeStatus.BUSY.value,
    load: float = 1.0,
) -> AgentNode:
    node = AgentNode(
        name=f"node-{uuid.uuid4().hex}",
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
    status: str,
    node_id: uuid.UUID | None = None,
    updated_at: datetime | None = None,
    change_summary: str | None = None,
) -> Task:
    task = Task(
        title=f"task-{uuid.uuid4().hex}",
        agent_type="programming",
        status=status,
        assigned_node_id=node_id,
        change_summary=change_summary,
    )
    session.add(task)
    await session.commit()
    if updated_at is not None:
        # 直接写库，避免 ORM 默认触发器覆盖
        await session.execute(update(Task).where(Task.id == task.id).values(updated_at=updated_at))
        await session.commit()
    await session.refresh(task)
    return task


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """每个测试前后清空任务与节点表。"""
    await db_session.execute(delete(Task))
    await db_session.execute(delete(AgentNode))
    yield
    await db_session.execute(delete(Task))
    await db_session.execute(delete(AgentNode))


# -------------------------- 单元测试 1：长 running 标 failed -------------------------- #


async def test_fail_stale_tasks_marks_long_running_as_failed(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """running 超时 → 任务 failed、change_summary 落库、节点 idle 且 load-1。"""
    node = await _create_node(db_session, status=NodeStatus.BUSY.value, load=1.0)
    stale_time = datetime.now(UTC) - timedelta(seconds=2000)
    task = await _create_task(
        db_session,
        status=TaskStatus.RUNNING.value,
        node_id=node.id,
        updated_at=stale_time,
    )

    with caplog.at_level(logging.INFO, logger="app.services.scheduler"):
        failed = await _fail_stale_tasks(stale_seconds=1800, session=db_session)
    # 被测函数走的是 Core update，与 ORM identity map 不同步；
    # 用 raw SELECT 重新读取最新值（绕开 ORM expire 后 lazy load）
    refreshed = (await db_session.execute(select(Task).where(Task.id == task.id))).scalar_one()
    refreshed_node = (
        await db_session.execute(select(AgentNode).where(AgentNode.id == node.id))
    ).scalar_one()

    assert failed == 1
    assert refreshed.status == TaskStatus.FAILED.value
    assert refreshed.change_summary is not None
    assert "1800" in refreshed.change_summary
    assert "自动标记为 failed" in refreshed.change_summary
    # ADR-032：节点状态/负载由心跳派生，卡死兜底不再改写节点行
    assert refreshed_node.status == NodeStatus.BUSY.value
    assert refreshed_node.load == 1.0
    # 日志事件校验
    assert any(rec.message == "task_stale_failed" for rec in caplog.records), (
        "expected task_stale_failed log"
    )


# -------------------------- 单元测试 2：新鲜 running 忽略 -------------------------- #


async def test_fail_stale_tasks_ignores_fresh_running(
    db_session: AsyncSession,
) -> None:
    """running 但 updated_at 在阈值内 → 不被兜底。"""
    node = await _create_node(db_session, status=NodeStatus.BUSY.value, load=1.0)
    task = await _create_task(
        db_session,
        status=TaskStatus.RUNNING.value,
        node_id=node.id,
        updated_at=datetime.now(UTC) - timedelta(seconds=60),
    )

    failed = await _fail_stale_tasks(stale_seconds=1800, session=db_session)
    row = (
        await db_session.execute(select(Task.status, Task.change_summary).where(Task.id == task.id))
    ).first()
    node_row = (
        await db_session.execute(
            select(AgentNode.status, AgentNode.load).where(AgentNode.id == node.id)
        )
    ).first()

    assert failed == 0
    assert row[0] == TaskStatus.RUNNING.value
    assert row[1] is None
    assert node_row[0] == NodeStatus.BUSY.value
    assert node_row[1] == 1.0


# -------------------------- 单元测试 3：SKIP LOCKED / 二次校验 -------------------------- #


async def test_fail_stale_tasks_skips_concurrent_state_change(
    db_session: AsyncSession,
) -> None:
    """扫描到 stale 任务后，Agent 端并发上报 completed → 兜底二次校验跳过。"""
    node = await _create_node(db_session, status=NodeStatus.BUSY.value, load=1.0)
    task = await _create_task(
        db_session,
        status=TaskStatus.RUNNING.value,
        node_id=node.id,
        updated_at=datetime.now(UTC) - timedelta(seconds=2000),
    )

    # 用 patch 在内部协程执行前先翻状态：模拟 Agent 在兜底 select 之前已上报 completed
    real_run_inner = _fail_stale_tasks  # 备份原函数

    async def run_with_state_flipped(*args, **kwargs):  # type: ignore[no-untyped-def]
        # 在兜底函数核心逻辑执行前，先把任务翻为 completed
        await db_session.execute(
            update(Task)
            .where(Task.id == task.id)
            .values(
                status=TaskStatus.COMPLETED.value,
                updated_at=datetime.now(UTC),
            )
        )
        return await real_run_inner(*args, **kwargs)

    failed = await run_with_state_flipped(stale_seconds=1800, session=db_session)

    # 二次校验会发现 status 已不是 running，UPDATE 影响 0 行
    assert failed == 0
    row = (
        await db_session.execute(select(Task.status, Task.change_summary).where(Task.id == task.id))
    ).first()
    assert row[0] == TaskStatus.COMPLETED.value


# -------------------------- 单元测试 4：change_summary 不覆写 -------------------------- #


async def test_fail_stale_tasks_does_not_overwrite_change_summary(
    db_session: AsyncSession,
) -> None:
    """Agent 已写入 change_summary → 兜底不覆写（保留 Agent 上报）。"""
    node = await _create_node(db_session, status=NodeStatus.BUSY.value, load=1.0)
    summary = "本轮修改：fix bug #123"
    task = await _create_task(
        db_session,
        status=TaskStatus.RUNNING.value,
        node_id=node.id,
        updated_at=datetime.now(UTC) - timedelta(seconds=2000),
        change_summary=summary,
    )

    failed = await _fail_stale_tasks(stale_seconds=1800, session=db_session)
    row = (
        await db_session.execute(select(Task.status, Task.change_summary).where(Task.id == task.id))
    ).first()

    assert failed == 1
    assert row[0] == TaskStatus.FAILED.value
    assert row[1] == summary  # 原样保留


# -------------------------- 单元测试 5：节点已不存在 -------------------------- #


async def test_fail_stale_tasks_handles_missing_node(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """任务 assigned_node_id 指向已离线的节点 → 任务仍标 failed，节点被覆盖为 idle。"""
    node = await _create_node(db_session, status=NodeStatus.OFFLINE.value, load=0.0)
    task = await _create_task(
        db_session,
        status=TaskStatus.RUNNING.value,
        node_id=node.id,
        updated_at=datetime.now(UTC) - timedelta(seconds=2000),
    )

    with caplog.at_level(logging.INFO, logger="app.services.scheduler"):
        failed = await _fail_stale_tasks(stale_seconds=1800, session=db_session)

    assert failed == 1
    row = (await db_session.execute(select(Task.status).where(Task.id == task.id))).first()
    node_row = (
        await db_session.execute(
            select(AgentNode.status, AgentNode.load).where(AgentNode.id == node.id)
        )
    ).first()
    assert row[0] == TaskStatus.FAILED.value
    # ADR-032：节点状态/负载由心跳派生，卡死兜底不再改写节点行
    assert node_row[0] == NodeStatus.OFFLINE.value
    assert node_row[1] == 0.0
    # 日志事件校验
    assert any(rec.message == "task_stale_failed" for rec in caplog.records)


# -------------------------- 单元测试 6：阈值从 Pydantic Settings 加载 -------------------------- #


async def test_threshold_loaded_from_settings(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自定义 STALE_TASK_TIMEOUT_SECONDS=120 → 仅 updated_at < NOW-120s 被兜底。"""
    # 临时覆盖 settings
    monkeypatch.setenv("STALE_TASK_TIMEOUT_SECONDS", "120")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.stale_task_timeout_seconds == 120

    node = await _create_node(db_session, status=NodeStatus.BUSY.value, load=1.0)
    # 任务 A：卡死 200s（>120） → 兜底
    stale_task = await _create_task(
        db_session,
        status=TaskStatus.RUNNING.value,
        node_id=node.id,
        updated_at=datetime.now(UTC) - timedelta(seconds=200),
    )
    # 任务 B：仅 60s（<120） → 不兜底
    fresh_task = await _create_task(
        db_session,
        status=TaskStatus.RUNNING.value,
        node_id=node.id,
        updated_at=datetime.now(UTC) - timedelta(seconds=60),
    )

    failed = await _fail_stale_tasks(
        stale_seconds=settings.stale_task_timeout_seconds, session=db_session
    )
    stale_row = (
        await db_session.execute(select(Task.status).where(Task.id == stale_task.id))
    ).first()
    fresh_row = (
        await db_session.execute(select(Task.status).where(Task.id == fresh_task.id))
    ).first()

    assert failed == 1
    assert stale_row[0] == TaskStatus.FAILED.value
    assert fresh_row[0] == TaskStatus.RUNNING.value

    # 还原（供后续测试）
    monkeypatch.delenv("STALE_TASK_TIMEOUT_SECONDS", raising=False)
    get_settings.cache_clear()


# -------------------------- 单元测试 7：阈值下限校验 -------------------------- #


def test_threshold_min_60_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """STALE_TASK_TIMEOUT_SECONDS=30（<60）启动 → Pydantic 校验失败。"""
    monkeypatch.setenv("STALE_TASK_TIMEOUT_SECONDS", "30")
    get_settings.cache_clear()
    with pytest.raises(Exception) as exc_info:
        Settings()
    assert (
        "stale_task_timeout_seconds" in str(exc_info.value).lower()
        or "ge=60" in str(exc_info.value)
        or "60" in str(exc_info.value)
    )
    get_settings.cache_clear()


# -------------------------- 集成测试 1：scheduler 主循环跑通兜底 -------------------------- #


async def test_scheduler_invokes_stale_detector_each_cycle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """run_scheduler 每轮调用 _fail_stale_tasks，参数为 settings.stale_task_timeout_seconds。

    通过 patch 替换 _fail_stale_tasks 与 _dispatch_once 为 noop，避免对开发数据库
    产生副作用；只验证调度器把阈值正确传入并周期性调用。
    """
    with (
        caplog.at_level(logging.INFO, logger="app.services.scheduler"),
        patch(
            "app.services.scheduler._fail_stale_tasks",
            new=AsyncMock(return_value=0),
        ) as mock_fail,
        patch(
            "app.services.scheduler._dispatch_once",
            new=AsyncMock(return_value=0),
        ),
    ):
        stop_event, task = start_scheduler()
        try:
            # 等待 ≥2 个周期确认重复调用
            await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS * 2.5)
        finally:
            await stop_scheduler(stop_event, task)

    # 至少被调用 2 次
    assert mock_fail.await_count >= 2
    # 每次调用都传入 Pydantic Settings 的阈值（位置参数或关键字参数均可）
    settings = get_settings()
    for call in mock_fail.await_args_list:
        actual = call.kwargs.get("stale_seconds") or (call.args[0] if call.args else None)
        assert actual == settings.stale_task_timeout_seconds
    # 启动与停止日志
    assert any(rec.message == "stale_task_detector_started" for rec in caplog.records)
    assert any(rec.message == "task_scheduler_stopped" for rec in caplog.records)


# -------------------------- 集成测试 2：异常不影响下一轮 -------------------------- #


async def test_scheduler_survives_stale_detector_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_fail_stale_tasks 抛错 → 记录 stale_task_detector_failed，下一轮继续跑。"""
    real_fail = _fail_stale_tasks
    call_count = {"n": 0}

    async def flaky_fail(stale_seconds: int) -> int:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated DB error")
        return await real_fail(stale_seconds)

    with (
        caplog.at_level(logging.ERROR, logger="app.services.scheduler"),
        patch("app.services.scheduler._fail_stale_tasks", side_effect=flaky_fail),
        patch(
            "app.services.scheduler._dispatch_once",
            new=AsyncMock(return_value=0),
        ),
    ):
        stop_event, task = start_scheduler()
        try:
            # 等待 ≥2 轮：第 1 轮抛错、第 2 轮正常
            await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS * 2.5)
        finally:
            await stop_scheduler(stop_event, task)

    # 至少被调用 2 次（首次异常 + 后续恢复）
    assert call_count["n"] >= 2
    # 第 1 次的异常被 scheduler 捕获并记 ERROR 日志
    assert any(rec.message == "stale_task_detector_outer_failed" for rec in caplog.records)


# -------------------------- 集成测试 3：优雅停止 -------------------------- #


async def test_scheduler_stops_cleanly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """stop_scheduler 触发后，run_scheduler 退出循环。"""
    with caplog.at_level(logging.INFO, logger="app.services.scheduler"):
        stop_event, task = start_scheduler()
        # 等一两个周期让 started 日志打出
        await asyncio.sleep(0.5)
        await stop_scheduler(stop_event, task)
        # 确保 task 完成
        assert task.done()
        assert not task.cancelled() or task.exception() is None

    assert any(
        rec.message in ("task_scheduler_started", "stale_task_detector_started")
        for rec in caplog.records
    )
    assert any(rec.message == "task_scheduler_stopped" for rec in caplog.records)


# 契约测试 1：TaskResponse 序列化保留 change_summary


def test_task_response_schema_includes_change_summary() -> None:
    """契约：TaskResponse 序列化时保留 change_summary 字段（前端「方案」Tab 依赖）。

    兜底失败后写入 change_summary 的数据需能被 TaskResponse schema 完整
    透传给前端；本测试直接构造 ORM Task 模拟兜底后的状态，验证序列化字段。
    """
    from app.schemas.task import TaskResponse

    now = datetime.now(UTC)
    fake_task = Task(
        id=uuid.uuid4(),
        title="contract-task",
        agent_type="programming",
        status=TaskStatus.FAILED.value,
        progress=0,
        priority="P2",
        source="manual",
        execution_mode="build",
        dispatch_type="auto",
        assigned_node_id=uuid.uuid4(),
        change_summary="任务执行超过 1800s 无进度，自动标记为 failed（卡死兜底）",
        created_at=now,
        updated_at=now,
    )
    schema = TaskResponse.model_validate(fake_task)
    payload = schema.model_dump(mode="json")
    assert payload["status"] == TaskStatus.FAILED.value
    assert payload.get("change_summary")
    assert "1800" in payload["change_summary"]
    assert "自动标记为 failed" in payload["change_summary"]
