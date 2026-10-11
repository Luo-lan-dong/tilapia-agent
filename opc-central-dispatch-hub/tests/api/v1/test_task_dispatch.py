"""任务派发与 mock 子系统接收 API 测试。"""

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.task_dispatch import (
    accept_task,
    approve_plan,
    dispatch_task,
    reject_plan,
)
from app.core.errors import TaskDispatchError
from app.main import create_app
from app.models.task import Task
from app.schemas.task_dispatch import (
    AcceptTaskRequest,
    RejectPlanRequest,
    TaskDispatchRequest,
)
from app.services.plan_review_service import PlanReviewService
from app.services.task_service import TaskService
from tests.api.v1.test_tasks import _noop_start_background_task


@pytest_asyncio.fixture
async def http_client() -> AsyncClient:
    """创建 HTTP 级异步测试客户端。"""
    with (
        patch("app.main.start_scheduler", _noop_start_background_task),
        patch("app.main.start_heartbeat_monitor", _noop_start_background_task),
    ):
        app = create_app()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


async def test_accept_task_mock(http_client: AsyncClient) -> None:
    """测试子系统接收任务 mock 接口返回固定响应。"""
    response = await accept_task(
        subsystem_id="programming",
        request=AcceptTaskRequest(
            task_id="123e4567-e89b-12d3-a456-426614174000",
            specification={"spec": "# Spec"},
        ),
    )

    assert response.code == 0
    assert response.data is not None
    assert response.data.status == "accepted"
    assert str(response.data.task_id) == "123e4567-e89b-12d3-a456-426614174000"


async def test_http_accept_task_mock(http_client: AsyncClient) -> None:
    """HTTP 层测试子系统接收任务 mock 接口。"""
    response = await http_client.post(
        "/api/v1/subsystems/programming/accept-task",
        json={
            "task_id": "123e4567-e89b-12d3-a456-426614174000",
            "specification": {"spec": "# Spec"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "accepted"


async def test_dispatch_task_success(db_session: AsyncSession) -> None:
    """测试手动派发任务：重新入队等待调度器分配真实节点。"""
    task = Task(
        title="登录接口开发",
        description="开发一个登录接口",
        agent_type="programming",
        status="completed",
        progress=100,
        specification={
            "spec": "# Spec",
            "tasks": "# Tasks",
            "checklist": "# Checklist",
        },
        source="spec_chat",
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    response = await dispatch_task(
        task_id=task.id,
        request=TaskDispatchRequest(target="programming"),
        session=db_session,
        service=TaskService(),
    )

    assert response.code == 0
    assert response.data is not None
    assert response.data.status == "queued"
    assert response.data.target == "programming"

    await db_session.refresh(task)
    assert task.execution_target == "programming"
    assert task.status == "pending"
    assert task.progress == 0


async def test_dispatch_task_running_rejected(db_session: AsyncSession) -> None:
    """测试正在执行中的任务不允许重新派发。"""
    task = Task(
        title="执行中任务",
        description="正在执行",
        agent_type="programming",
        status="running",
        progress=50,
        specification={"spec": "# Spec"},
        source="spec_chat",
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    with pytest.raises(TaskDispatchError) as exc_info:
        await dispatch_task(
            task_id=task.id,
            request=TaskDispatchRequest(target="programming"),
            session=db_session,
            service=TaskService(),
        )
    assert "正在执行中" in str(exc_info.value)


async def test_dispatch_task_missing_specification(db_session: AsyncSession) -> None:
    """测试缺少 specification 的任务派发抛出 TaskDispatchError。"""
    task = Task(
        title="无规范任务",
        description="没有三件套",
        agent_type="programming",
        status="pending",
        progress=0,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    with pytest.raises(TaskDispatchError) as exc_info:
        await dispatch_task(
            task_id=task.id,
            request=TaskDispatchRequest(target="programming"),
            session=db_session,
            service=TaskService(),
        )

    assert "缺少规范" in str(exc_info.value)


def _make_plan_task(
    execution_mode: str = "plan",
    task_status: str = "completed",
    plan_content: str | None = "# 规划方案\n1. 重构登录模块\n2. 新增记住我功能",
) -> Task:
    """构造用于 plan 审批流测试的任务实例。"""
    return Task(
        title="规划任务",
        description="先出方案再开发",
        agent_type="programming",
        status=task_status,
        progress=100 if task_status in ("completed", "review") else 0,
        specification={
            "spec": "# Spec",
            "tasks": "# Tasks",
            "checklist": "# Checklist",
        },
        source="spec_chat",
        execution_mode=execution_mode,
        execution_target="programming",
        plan_content=plan_content,
    )


async def test_approve_plan_success(db_session: AsyncSession) -> None:
    """测试批准规划方案：mode 切 build、方案拼入 change_brief、任务回 pending。"""
    task = _make_plan_task()
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    response = await approve_plan(
        task_id=task.id,
        session=db_session,
        service=PlanReviewService(),
    )

    assert response.code == 0
    assert response.data is not None
    assert response.data.status == "queued"
    assert response.data.target == "programming"

    await db_session.refresh(task)
    assert task.execution_mode == "build"
    assert "规划方案已批准" in task.change_brief
    assert task.plan_content in task.change_brief
    assert task.status == "pending"
    assert task.progress == 0


async def test_approve_plan_review_status_success(db_session: AsyncSession) -> None:
    """ADR-038：review（待确认）状态的 plan 任务可批准，转 build 重新入队。"""
    task = _make_plan_task(task_status="review")
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    response = await approve_plan(
        task_id=task.id,
        session=db_session,
        service=PlanReviewService(),
    )

    assert response.code == 0
    assert response.data is not None
    assert response.data.status == "queued"
    await db_session.refresh(task)
    assert task.execution_mode == "build"
    assert task.status == "pending"


async def test_reject_plan_review_status_success(db_session: AsyncSession) -> None:
    """ADR-038：review 状态的 plan 任务可驳回，以 plan 模式重新入队。"""
    task = _make_plan_task(task_status="review")
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    response = await reject_plan(
        task_id=task.id,
        request=RejectPlanRequest(feedback="补充测试计划"),
        session=db_session,
        service=PlanReviewService(),
    )

    assert response.code == 0
    await db_session.refresh(task)
    assert task.execution_mode == "plan"
    assert "补充测试计划" in task.change_brief
    assert task.status == "pending"


async def test_approve_plan_build_task_rejected(db_session: AsyncSession) -> None:
    """测试 build 模式任务不允许批准规划方案。"""
    task = _make_plan_task(execution_mode="build")
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    with pytest.raises(TaskDispatchError) as exc_info:
        await approve_plan(task_id=task.id, session=db_session, service=PlanReviewService())

    assert "仅 plan 模式" in str(exc_info.value)


async def test_approve_plan_not_completed_rejected(db_session: AsyncSession) -> None:
    """测试未完成任务不允许批准规划方案。"""
    task = _make_plan_task(task_status="running")
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    with pytest.raises(TaskDispatchError) as exc_info:
        await approve_plan(task_id=task.id, session=db_session, service=PlanReviewService())

    assert "尚未完成" in str(exc_info.value)


async def test_approve_plan_missing_plan_content_rejected(
    db_session: AsyncSession,
) -> None:
    """测试未上报方案内容的任务不允许批准。"""
    task = _make_plan_task(plan_content=None)
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    with pytest.raises(TaskDispatchError) as exc_info:
        await approve_plan(task_id=task.id, session=db_session, service=PlanReviewService())

    assert "未上报规划方案" in str(exc_info.value)


async def test_reject_plan_success(db_session: AsyncSession) -> None:
    """测试驳回规划方案：反馈拼入 change_brief、mode 保持 plan、任务回 pending。"""
    task = _make_plan_task()
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    response = await reject_plan(
        task_id=task.id,
        request=RejectPlanRequest(feedback="缺少性能优化章节"),
        session=db_session,
        service=PlanReviewService(),
    )

    assert response.code == 0
    assert response.data is not None
    assert response.data.status == "queued"

    await db_session.refresh(task)
    assert task.execution_mode == "plan"
    assert "上一轮规划方案被驳回" in task.change_brief
    assert "缺少性能优化章节" in task.change_brief
    assert task.status == "pending"
    assert task.progress == 0


async def test_reject_plan_build_task_rejected(db_session: AsyncSession) -> None:
    """测试 build 模式任务不允许驳回规划方案。"""
    task = _make_plan_task(execution_mode="build")
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    with pytest.raises(TaskDispatchError) as exc_info:
        await reject_plan(
            task_id=task.id,
            request=RejectPlanRequest(feedback="意见"),
            session=db_session,
            service=PlanReviewService(),
        )

    assert "仅 plan 模式" in str(exc_info.value)
