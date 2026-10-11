"""Agent 节点管理 API 端点（安装脚本、详情、Token 重置）。

注：本文件超过 250 行路由上限（见 docs/standards/coding-standards.md 4.3）。
各端点仅做参数转发，业务逻辑已下沉 service 层；剩余行数主要为端点
装饰器元数据（summary/description/响应模型声明），继续拆分只会分散
路由注册，故按豁免场景保留并在此说明。
"""

import logging
import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_async_session,
    get_settings,
    get_system_settings_service,
)
from app.core.config import Settings
from app.schemas.agent_node import (
    AgentNodeMetricsResponse,
    AgentNodeResponse,
    AgentNodeUpdateRequest,
    InstallScriptResponse,
    RegistrationTokenResponse,
)
from app.schemas.common import ApiResponse
from app.services.agent_node_management_service import AgentNodeManagementService
from app.services.system_settings_service import SystemSettingsService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Agent 节点管理"])


@router.get(
    "/install-scripts",
    response_model=ApiResponse[InstallScriptResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="获取安装脚本信息",
    description="获取指定 Agent 类型和操作系统的安装脚本下载地址、校验和及版本。",
)
async def get_install_script(
    request: Request,
    agent_type: str = Query(..., description="Agent 类型，如 programming"),
    os: str = Query(..., description="目标操作系统，如 linux"),
    session: AsyncSession = Depends(get_async_session),
    settings: Settings = Depends(get_settings),
    service: AgentNodeManagementService = Depends(),
    system_settings_service: SystemSettingsService = Depends(get_system_settings_service),
) -> ApiResponse[InstallScriptResponse]:
    """获取 Agent 安装脚本元数据。

    Args:
        request: FastAPI 请求对象，用于推导基础 URL。
        agent_type: Agent 类型。
        os: 目标操作系统。
        session: 异步数据库会话。
        settings: 应用配置。
        service: 节点管理服务实例。
        system_settings_service: 系统设置服务实例。

    Returns:
        统一响应，data 为安装脚本元数据。
    """
    logger.info(
        "install_script_get_started",
        extra={
            "event": "install_script_get_started",
            "agent_type": agent_type,
            "os": os,
        },
    )
    system_settings = await system_settings_service.get_network_settings(session)
    hub_url = (
        system_settings.hub_public_url or settings.hub_public_url or str(request.base_url)
    ).rstrip("/")
    logger.info(
        "get_install_script_hub_url_resolved",
        extra={
            "event": "get_install_script_hub_url_resolved",
            "agent_type": agent_type,
            "os": os,
            "hub_url": hub_url,
            "source": "database"
            if system_settings.hub_public_url
            else ("env" if settings.hub_public_url else "request"),
        },
    )
    data = await service.get_install_script(agent_type, os, hub_url)
    logger.info(
        "install_script_get_completed",
        extra={
            "event": "install_script_get_completed",
            "agent_type": agent_type,
            "os": os,
            "hub_url": hub_url,
            "script_url": data.script_url,
        },
    )
    return ApiResponse(code=0, message="ok", data=data)


@router.get(
    "/{node_id}",
    response_model=ApiResponse[AgentNodeResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="查询 Agent 节点详情",
    description="根据节点 ID 获取 Agent 节点详情、状态及最后心跳时间。",
)
async def get_agent_node(
    node_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: AgentNodeManagementService = Depends(),
) -> ApiResponse[AgentNodeResponse]:
    """查询 Agent 节点详情。

    Args:
        node_id: 节点 UUID。
        session: 异步数据库会话。
        service: 节点管理服务实例。

    Returns:
        统一响应，data 为节点详情。
    """
    logger.info(
        "agent_node_get_started",
        extra={
            "event": "agent_node_get_started",
            "node_id": str(node_id),
        },
    )
    try:
        node = await service.get_agent_node(session, node_id)
    except Exception:
        logger.error(
            "agent_node_get_failed",
            extra={
                "event": "agent_node_get_failed",
                "node_id": str(node_id),
            },
            exc_info=True,
        )
        raise
    logger.info(
        "agent_node_get_completed",
        extra={
            "event": "agent_node_get_completed",
            "client_id": str(node.id),
        },
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=AgentNodeResponse.model_validate(node),
    )


@router.post(
    "/{node_id}/token",
    response_model=ApiResponse[RegistrationTokenResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="重新生成注册 Token",
    description="为指定 Agent 节点重新生成 registration_token。",
)
async def regenerate_registration_token(
    node_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: AgentNodeManagementService = Depends(),
) -> ApiResponse[RegistrationTokenResponse]:
    """重新生成节点注册 Token。

    Args:
        node_id: 节点 UUID。
        session: 异步数据库会话。
        service: 节点管理服务实例。

    Returns:
        统一响应，data 为新的 registration_token。
    """
    logger.info(
        "agent_token_regenerate_started",
        extra={
            "event": "agent_token_regenerate_started",
            "node_id": str(node_id),
        },
    )
    try:
        data = await service.regenerate_registration_token(session, node_id)
    except Exception:
        logger.error(
            "agent_token_regenerate_failed",
            extra={
                "event": "agent_token_regenerate_failed",
                "node_id": str(node_id),
            },
            exc_info=True,
        )
        raise
    logger.info(
        "agent_token_regenerated",
        extra={
            "event": "agent_token_regenerated",
            "client_id": str(node_id),
        },
    )
    return ApiResponse(code=0, message="ok", data=data)


@router.get(
    "/{node_id}/metrics",
    response_model=ApiResponse[AgentNodeMetricsResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="查询 Agent 节点监控详情",
    description="根据节点 ID 获取节点实时系统资源监控快照，包括 CPU、内存、磁盘、网络及综合负载得分。",
)
async def get_agent_node_metrics(
    node_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: AgentNodeManagementService = Depends(),
) -> ApiResponse[AgentNodeMetricsResponse]:
    """查询 Agent 节点监控详情。

    Args:
        node_id: 节点 UUID。
        session: 异步数据库会话。
        service: 节点管理服务实例。

    Returns:
        统一响应，data 为节点监控详情。
    """
    logger.info(
        "agent_node_metrics_get_started",
        extra={
            "event": "agent_node_metrics_get_started",
            "node_id": str(node_id),
        },
    )
    try:
        data = await service.get_node_metrics(session, node_id)
    except Exception:
        logger.error(
            "agent_node_metrics_get_failed",
            extra={
                "event": "agent_node_metrics_get_failed",
                "node_id": str(node_id),
            },
            exc_info=True,
        )
        raise
    logger.info(
        "agent_node_metrics_get_completed",
        extra={
            "event": "agent_node_metrics_get_completed",
            "client_id": str(node_id),
            "is_online": data.is_online,
        },
    )
    return ApiResponse(code=0, message="ok", data=data)


@router.patch(
    "/{node_id}",
    response_model=ApiResponse[AgentNodeResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="更新 Agent 节点显示名",
    description=(
        "更新 Agent 节点显示名（仅 name 字段）。agent_type / client_id "
        "等调度路由相关字段不可改。MVP 阶段不引入鉴权。"
    ),
)
async def update_agent_node(
    node_id: uuid.UUID,
    payload: AgentNodeUpdateRequest,
    session: AsyncSession = Depends(get_async_session),
    service: AgentNodeManagementService = Depends(),
) -> ApiResponse[AgentNodeResponse]:
    """更新 Agent 节点显示名（ADR-016）。

    Args:
        node_id: 节点 UUID。
        payload: PATCH 请求体（仅含 name）。
        session: 异步数据库会话。
        service: 节点管理服务实例。

    Returns:
        统一响应，data 为更新后的节点详情。
    """
    logger.info(
        "node_name_update_started",
        extra={
            "event": "node_name_update_started",
            "node_id": str(node_id),
            "requested_name": payload.name,
        },
    )
    try:
        node = await service.update_node_name(session, node_id, payload)
    except Exception:
        logger.error(
            "node_name_update_failed",
            extra={
                "event": "node_name_update_failed",
                "node_id": str(node_id),
                "requested_name": payload.name,
            },
            exc_info=True,
        )
        raise
    logger.info(
        "node_name_update_completed",
        extra={
            "event": "node_name_update_completed",
            "node_id": str(node_id),
            "new_name": node.name,
        },
    )
    return ApiResponse(code=0, message="ok", data=node)
