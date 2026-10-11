"""Agent 节点注册与列表查询 API 端点。"""

import logging

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_session
from app.schemas.agent_node import (
    AgentNodeListResponse,
    AgentNodeRegisterRequest,
    AgentNodeResponse,
)
from app.schemas.common import ApiResponse
from app.services.agent_node_service import AgentNodeService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent 节点管理"])


@router.post(
    "/register",
    response_model=ApiResponse[AgentNodeResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_201_CREATED,
    summary="注册 Agent 节点",
    description="Agent 节点启动后向中枢注册，获取 node_id 与 registration_token。",
)
async def register_agent_node(
    request: AgentNodeRegisterRequest,
    session: AsyncSession = Depends(get_async_session),
    service: AgentNodeService = Depends(),
) -> ApiResponse[AgentNodeResponse]:
    """注册 Agent 节点。

    Args:
        request: 注册请求，包含节点名称、类型与能力标签。
        session: 异步数据库会话。
        service: Agent 节点服务实例。

    Returns:
        统一响应，data 为注册成功的节点详情（含 registration_token）。
    """
    logger.info(
        "agent_register_started",
        extra={
            "event": "agent_register_started",
            "agent_type": request.agent_type,
        },
    )
    try:
        data = await service.register_agent_node(session, request)
    except Exception:
        logger.exception(
            "agent_register_failed",
            extra={"event": "agent_register_failed"},
        )
        raise
    logger.info(
        "agent_registered",
        extra={
            "event": "agent_registered",
            "client_id": str(data.id),
            "agent_type": data.agent_type,
            "capabilities": data.capabilities,
        },
    )
    return ApiResponse(code=0, message="ok", data=data)


@router.get(
    "",
    response_model=ApiResponse[AgentNodeListResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="查询 Agent 节点列表",
    description="分页查询 Agent 节点列表，支持按状态和 Agent 类型筛选。",
)
async def list_agent_nodes(
    status: str | None = Query(None, description="节点状态筛选"),
    agent_type: str | None = Query(None, description="Agent 类型筛选"),
    limit: int = Query(50, ge=1, le=200, description="每页数量，默认 50，最大 200"),
    offset: int = Query(0, ge=0, description="偏移量，默认 0"),
    session: AsyncSession = Depends(get_async_session),
    service: AgentNodeService = Depends(),
) -> ApiResponse[AgentNodeListResponse]:
    """分页查询 Agent 节点列表。

    Args:
        status: 可选，按节点状态筛选。
        agent_type: 可选，按 Agent 类型筛选。
        limit: 每页返回条数，范围 1~200。
        offset: 分页偏移量，必须大于等于 0。
        session: 异步数据库会话。
        service: Agent 节点服务实例。

    Returns:
        统一响应，data 包含节点列表和总条数。
    """
    logger.info(
        "agent_node_list_started",
        extra={"event": "agent_node_list_started"},
    )
    data = await service.list_agent_nodes(
        session,
        status_filter=status,
        agent_type=agent_type,
        limit=limit,
        offset=offset,
    )
    logger.info(
        "agent_node_list_completed",
        extra={
            "event": "agent_node_list_completed",
            "count": data.pagination.total,
        },
    )
    return ApiResponse(code=0, message="ok", data=data)
