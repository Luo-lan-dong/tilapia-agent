"""任务构建会话 API。"""

import logging
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_session
from app.core.errors import BusinessError, ErrorCode
from app.models.task_building_session import TaskBuildingSession
from app.schemas.common import ApiResponse
from app.schemas.task import OrchestratorTodoItem, OrchestratorTodoListResponse
from app.schemas.task_building import (
    TaskBuildingSessionCreateRequest,
    TaskBuildingSessionListResponse,
    TaskBuildingSessionResponse,
    TaskBuildingSessionUpdateRequest,
)
from app.services import orchestrator_todo_service, wake_stream_service
from app.services.task_building_session_service import TaskBuildingSessionService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["任务构建"])


def get_session_service(
    session: AsyncSession = Depends(get_async_session),
) -> TaskBuildingSessionService:
    """获取任务构建会话服务实例依赖。"""
    return TaskBuildingSessionService(session)


@router.post(
    "/sessions",
    response_model=ApiResponse[TaskBuildingSessionResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_201_CREATED,
    summary="创建任务构建会话",
)
async def create_session(
    request: TaskBuildingSessionCreateRequest,
    service: TaskBuildingSessionService = Depends(get_session_service),
) -> ApiResponse[TaskBuildingSessionResponse]:
    """创建新的任务构建会话。"""
    logger.info(
        "session_create_started",
        extra={"event": "session_create_started"},
    )
    try:
        session_obj = await service.create(request)
        logger.info(
            "session_created",
            extra={
                "event": "session_created",
                "session_id": str(session_obj.id),
            },
        )
        return ApiResponse(
            code=0,
            message="ok",
            data=TaskBuildingSessionResponse.model_validate(session_obj),
        )
    except Exception:
        logger.exception("session_create_failed")
        raise


@router.get(
    "/sessions",
    response_model=ApiResponse[TaskBuildingSessionListResponse],
    response_model_exclude_unset=True,
    summary="查询任务构建会话列表",
)
async def list_sessions(
    limit: int = Query(20, ge=1, le=200, description="每页数量，默认 20，最大 200"),
    offset: int = Query(0, ge=0, description="偏移量，默认 0"),
    service: TaskBuildingSessionService = Depends(get_session_service),
) -> ApiResponse[TaskBuildingSessionListResponse]:
    """按创建时间倒序分页查询会话列表。"""
    logger.info(
        "session_list_started",
        extra={"event": "session_list_started"},
    )
    data = await service.list(limit=limit, offset=offset)
    logger.info(
        "session_list_completed",
        extra={
            "event": "session_list_completed",
            "count": len(data.items),
            "total": data.pagination.total,
        },
    )
    return ApiResponse(code=0, message="ok", data=data)


@router.get(
    "/sessions/{session_id}",
    response_model=ApiResponse[TaskBuildingSessionResponse],
    response_model_exclude_unset=True,
    summary="查询任务构建会话详情",
)
async def get_session(
    session_id: uuid.UUID,
    service: TaskBuildingSessionService = Depends(get_session_service),
) -> ApiResponse[TaskBuildingSessionResponse]:
    """根据 ID 查询会话详情。"""
    logger.info(
        "session_get_started",
        extra={
            "event": "session_get_started",
            "session_id": str(session_id),
        },
    )
    session_obj = await service.get(session_id)
    if session_obj is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.TASK_BUILDING_SESSION_NOT_FOUND,
            detail="任务构建会话不存在",
        )
    logger.info(
        "session_get_completed",
        extra={
            "event": "session_get_completed",
            "session_id": str(session_id),
        },
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=TaskBuildingSessionResponse.model_validate(session_obj),
    )


@router.patch(
    "/sessions/{session_id}",
    response_model=ApiResponse[TaskBuildingSessionResponse],
    response_model_exclude_unset=True,
    summary="更新任务构建会话",
)
async def update_session(
    session_id: uuid.UUID,
    request: TaskBuildingSessionUpdateRequest,
    service: TaskBuildingSessionService = Depends(get_session_service),
) -> ApiResponse[TaskBuildingSessionResponse]:
    """更新会话的对话历史、三件套草案、模式或模型。"""
    logger.info(
        "session_update_started",
        extra={
            "event": "session_update_started",
            "session_id": str(session_id),
        },
    )
    try:
        session_obj = await service.update(session_id, request)
        if session_obj is None:
            raise BusinessError(
                status_code=status.HTTP_404_NOT_FOUND,
                biz_code=ErrorCode.TASK_BUILDING_SESSION_NOT_FOUND,
                detail="任务构建会话不存在",
            )
        logger.info(
            "session_updated",
            extra={
                "event": "session_updated",
                "session_id": str(session_id),
            },
        )
        return ApiResponse(
            code=0,
            message="ok",
            data=TaskBuildingSessionResponse.model_validate(session_obj),
        )
    except Exception:
        logger.exception("session_update_failed")
        raise


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除任务构建会话",
)
async def delete_session(
    session_id: uuid.UUID,
    service: TaskBuildingSessionService = Depends(get_session_service),
) -> None:
    """删除指定会话。"""
    logger.info(
        "session_delete_started",
        extra={
            "event": "session_delete_started",
            "session_id": str(session_id),
        },
    )
    try:
        deleted = await service.delete(session_id)
        if not deleted:
            raise BusinessError(
                status_code=status.HTTP_404_NOT_FOUND,
                biz_code=ErrorCode.TASK_BUILDING_SESSION_NOT_FOUND,
                detail="任务构建会话不存在",
            )
        logger.info(
            "session_deleted",
            extra={
                "event": "session_deleted",
                "session_id": str(session_id),
            },
        )
    except Exception:
        logger.exception("session_delete_failed")
        raise


@router.get(
    "/sessions/{session_id}/orchestrator-todos",
    response_model=ApiResponse[OrchestratorTodoListResponse],
    status_code=status.HTTP_200_OK,
    summary="获取本会话的中枢编排器待办（任务创建前挂会话）",
    description=(
        "ADR-067 待办先行：任务创建前编排 AI 的待办挂构建会话，任务创建成功后"
        "自动迁移归属到任务。本端点供前端在会话阶段（尚无任务）只读展示待办清单。"
    ),
)
async def list_session_orchestrator_todos(
    session_id: uuid.UUID,
    db_session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[OrchestratorTodoListResponse]:
    """读取挂在本会话名下的中枢待办（按 seq 升序；会话不存在返回 404）。"""
    session_obj = await db_session.get(TaskBuildingSession, session_id)
    if session_obj is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.TASK_BUILDING_SESSION_NOT_FOUND,
            detail=f"任务构建会话不存在：{session_id}",
        )
    rows = await orchestrator_todo_service.list_todos(db_session, session_id=session_id)
    completed = sum(1 for row in rows if row.status == "completed")
    return ApiResponse(
        code=0,
        message="ok",
        data=OrchestratorTodoListResponse(
            session_id=session_id,
            items=[OrchestratorTodoItem.model_validate(row) for row in rows],
            pending_count=len(rows) - completed,
            completed_count=completed,
        ),
    )


def _format_sse_event(event: str, data: dict) -> str:
    """将事件格式化为 SSE 数据块（与 task_building.py 同款协议）。"""
    import json

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get(
    "/sessions/{session_id}/wake-stream",
    response_class=StreamingResponse,
    status_code=status.HTTP_200_OK,
    summary="订阅唤醒轮事件流（SSE）",
    description=(
        "ADR-066 唤醒可视化（2026-10-07 用户裁决）：任务终态自动唤醒轮在后台"
        "执行时，本端点把编排事件（thinking/chunk/step/meta/done）实时转发给"
        "前端，对话区像正常对话一样流式展示中枢被唤醒后继续工作的过程。"
        "会话无活跃唤醒轮时立即返回 done 结束（前端快速收尾不悬挂）；"
        "完整历史由落库 + 前端轮询同步兜底。"
    ),
)
async def subscribe_wake_stream(
    session_id: uuid.UUID,
) -> StreamingResponse:
    """订阅会话的唤醒轮事件流（只读转发，不落库）。"""
    logger.info(
        "wake_stream_subscribed",
        extra={"event": "wake_stream_subscribed", "session_id": str(session_id)},
    )

    async def event_generator() -> AsyncGenerator[str, None]:
        async for event in wake_stream_service.subscribe(str(session_id)):
            yield _format_sse_event(event["event"], event["data"])

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
