"""任务派发与 mock 子系统接收 API。

路由只做参数转发，业务逻辑下沉到 service 层
（backend-component-standards §2.1）：
- 手动派发 → :class:`TaskService.dispatch_task`
- 方案批准/驳回 → :class:`PlanReviewService`
"""

import logging
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_session
from app.schemas.common import ApiResponse
from app.schemas.task_dispatch import (
    AcceptTaskRequest,
    AcceptTaskResponse,
    RejectPlanRequest,
    TaskDispatchRequest,
    TaskDispatchResponse,
)
from app.services.plan_review_service import PlanReviewService
from app.services.task_service import TaskService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["任务派发"])
subsystem_router = APIRouter(tags=["子系统 mock"])


@router.post(
    "/{task_id}/dispatch",
    response_model=ApiResponse[TaskDispatchResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="手动派发任务",
    description="将任务重新入队，由调度器分配给对应类型的空闲 Agent 节点。",
)
async def dispatch_task(
    task_id: uuid.UUID,
    request: TaskDispatchRequest,
    session: AsyncSession = Depends(get_async_session),
    service: TaskService = Depends(),
) -> ApiResponse[TaskDispatchResponse]:
    """手动派发任务入口。"""
    logger.info(
        "task_dispatch_started",
        extra={
            "event": "task_dispatch_started",
            "task_id": str(task_id),
            "target": request.target,
            "mode": request.mode,
        },
    )
    result = await service.dispatch_task(
        session,
        task_id,
        request.target,
        mode=request.mode,
        node_id=request.node_id,
        force=request.force,
    )
    logger.info(
        "task_dispatched",
        extra={
            "event": "task_dispatched",
            "task_id": str(task_id),
            "target": result["target"],
            "status": result["status"],
            "dispatch_type": result.get("dispatch_type"),
            "forced_dispatch": request.force,
            "queued_on_node": result.get("queued_on_node"),
        },
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=TaskDispatchResponse(
            task_id=task_id,
            target=result["target"],
            status=result["status"],
            dispatch_type=result.get("dispatch_type", "auto"),
            queued_on_node=result.get("queued_on_node"),
            hold_reason=result.get("hold_reason"),
        ),
    )


@router.post(
    "/{task_id}/approve-plan",
    response_model=ApiResponse[TaskDispatchResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="批准规划方案并转入开发",
    description=(
        "批准 plan 模式任务产出的规划方案：执行模式改为 build，方案全文拼入 "
        "change_brief，任务重新入队派发（Agent 在同一工作目录与会话上直接开发）。"
    ),
)
async def approve_plan(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: PlanReviewService = Depends(),
) -> ApiResponse[TaskDispatchResponse]:
    """批准规划方案入口（业务逻辑见 ``PlanReviewService.approve_plan``）。"""
    result = await service.approve_plan(session, task_id)
    return ApiResponse(
        code=0,
        message="ok",
        data=TaskDispatchResponse(
            task_id=task_id,
            target=result["target"],
            status=result["status"],
            dispatch_type=result.get("dispatch_type", "auto"),
        ),
    )


@router.post(
    "/{task_id}/reject-plan",
    response_model=ApiResponse[TaskDispatchResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="驳回规划方案并重新规划",
    description=(
        "驳回 plan 模式任务产出的规划方案：反馈意见拼入 change_brief，"
        "任务以 plan 模式重新入队派发，循环直至批准。"
    ),
)
async def reject_plan(
    task_id: uuid.UUID,
    request: RejectPlanRequest,
    session: AsyncSession = Depends(get_async_session),
    service: PlanReviewService = Depends(),
) -> ApiResponse[TaskDispatchResponse]:
    """驳回规划方案入口（业务逻辑见 ``PlanReviewService.reject_plan``）。"""
    result = await service.reject_plan(session, task_id, request.feedback)
    return ApiResponse(
        code=0,
        message="ok",
        data=TaskDispatchResponse(
            task_id=task_id,
            target=result["target"],
            status=result["status"],
            dispatch_type=result.get("dispatch_type", "auto"),
        ),
    )


@subsystem_router.post(
    "/{subsystem_id}/accept-task",
    response_model=ApiResponse[AcceptTaskResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="子系统接收任务（MVP mock）",
    description="MVP 阶段由中枢后端模拟子系统接收任务并返回固定响应。",
)
async def accept_task(
    subsystem_id: str,
    request: AcceptTaskRequest,
) -> ApiResponse[AcceptTaskResponse]:
    """子系统接收任务 mock 入口。"""
    logger.info(
        "subsystem_task_accept_started",
        extra={
            "event": "subsystem_task_accept_started",
            "task_id": str(request.task_id),
            "subsystem_id": subsystem_id,
        },
    )
    logger.info(
        "subsystem_task_accept_completed",
        extra={
            "event": "subsystem_task_accept_completed",
            "task_id": str(request.task_id),
            "subsystem_id": subsystem_id,
        },
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=AcceptTaskResponse(
            status="accepted",
            task_id=request.task_id,
        ),
    )
