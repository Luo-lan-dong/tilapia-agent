"""宝塔 MCP 服务器管理 API 端点（ADR-049）。"""

import logging
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_session, get_bt_mcp_server_service
from app.core.errors import BusinessError, ErrorCode
from app.schemas.bt_mcp import (
    BtMcpServerCreate,
    BtMcpServerListResponse,
    BtMcpServerResponse,
    BtMcpServerUpdate,
    BtMcpTestResponse,
    BtMcpToolsResponse,
)
from app.schemas.common import ApiResponse
from app.services.bt_mcp_server_service import BtMcpServerService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["宝塔 MCP"])


def _to_response(server) -> BtMcpServerResponse:
    """模型转脱敏响应（凭据只回布尔）。"""
    return BtMcpServerResponse(
        id=server.id,
        name=server.name,
        local_url=server.local_url,
        public_url=server.public_url,
        address_mode=server.address_mode,
        has_token=bool(server.api_token),
        has_ca_cert=bool(server.ca_cert),
        enabled=server.enabled,
        remark=server.remark,
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


@router.get(
    "",
    response_model=ApiResponse[BtMcpServerListResponse],
    response_model_exclude_unset=True,
    summary="查询宝塔 MCP 服务器列表",
    description="返回全部宝塔 MCP 服务器连接（凭据脱敏，ADR-049）。",
)
async def list_servers(
    session: AsyncSession = Depends(get_async_session),
    service: BtMcpServerService = Depends(get_bt_mcp_server_service),
) -> ApiResponse[BtMcpServerListResponse]:
    """查询服务器列表。"""
    servers = await service.list_servers(session)
    return ApiResponse(
        code=0,
        message="ok",
        data=BtMcpServerListResponse(
            total=len(servers),
            items=[_to_response(server) for server in servers],
        ),
    )


@router.post(
    "",
    response_model=ApiResponse[BtMcpServerResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_201_CREATED,
    summary="添加宝塔 MCP 服务器",
    description="手填字段或粘贴插件生成的 mcp_info JSON 一键导入（ADR-049）。",
)
async def create_server(
    data: BtMcpServerCreate,
    session: AsyncSession = Depends(get_async_session),
    service: BtMcpServerService = Depends(get_bt_mcp_server_service),
) -> ApiResponse[BtMcpServerResponse]:
    """添加服务器连接。"""
    try:
        server = await service.create_server(session, data)
    except ValueError as exc:
        raise BusinessError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            biz_code=ErrorCode.BT_MCP_CONFIG_INVALID,
            detail=str(exc),
        ) from exc
    return ApiResponse(code=0, message="ok", data=_to_response(server))


@router.post(
    "/{server_id}/test",
    response_model=ApiResponse[BtMcpTestResponse],
    response_model_exclude_unset=True,
    summary="测试宝塔 MCP 连接",
    description="握手 + 拉取工具清单；失败时 message 含排查指引（白名单/令牌/证书/端口）。",
)
async def test_connection(
    server_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: BtMcpServerService = Depends(get_bt_mcp_server_service),
) -> ApiResponse[BtMcpTestResponse]:
    """连接测试。"""
    server = await service.get_server(session, server_id)
    if server is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.BT_MCP_SERVER_NOT_FOUND,
            detail=f"宝塔 MCP 服务器不存在: {server_id}",
        )
    result = await service.test_connection(session, server)
    return ApiResponse(code=0, message="ok", data=result)


@router.get(
    "/{server_id}/tools",
    response_model=ApiResponse[BtMcpToolsResponse],
    response_model_exclude_unset=True,
    summary="查询服务器工具清单",
    description="实时从目标服务器拉取 MCP 工具清单（含分类与风险等级）。",
)
async def list_tools(
    server_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: BtMcpServerService = Depends(get_bt_mcp_server_service),
) -> ApiResponse[BtMcpToolsResponse]:
    """查询工具清单。"""
    server = await service.get_server(session, server_id)
    if server is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.BT_MCP_SERVER_NOT_FOUND,
            detail=f"宝塔 MCP 服务器不存在: {server_id}",
        )
    try:
        tools = await service.list_tools(session, server)
    except ValueError as exc:
        raise BusinessError(
            status_code=status.HTTP_400_BAD_REQUEST,
            biz_code=ErrorCode.BT_MCP_CONFIG_INVALID,
            detail=str(exc),
        ) from exc
    return ApiResponse(
        code=0,
        message="ok",
        data=BtMcpToolsResponse(total=len(tools), items=tools),
    )


@router.get(
    "/{server_id}",
    response_model=ApiResponse[BtMcpServerResponse],
    response_model_exclude_unset=True,
    summary="查询宝塔 MCP 服务器详情",
)
async def get_server(
    server_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: BtMcpServerService = Depends(get_bt_mcp_server_service),
) -> ApiResponse[BtMcpServerResponse]:
    """查询服务器详情。"""
    server = await service.get_server(session, server_id)
    if server is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.BT_MCP_SERVER_NOT_FOUND,
            detail=f"宝塔 MCP 服务器不存在: {server_id}",
        )
    return ApiResponse(code=0, message="ok", data=_to_response(server))


@router.patch(
    "/{server_id}",
    response_model=ApiResponse[BtMcpServerResponse],
    response_model_exclude_unset=True,
    summary="更新宝塔 MCP 服务器",
    description="凭据字段缺省保留现值、空串清除；可重新导入 mcp_info JSON。",
)
async def update_server(
    server_id: uuid.UUID,
    data: BtMcpServerUpdate,
    session: AsyncSession = Depends(get_async_session),
    service: BtMcpServerService = Depends(get_bt_mcp_server_service),
) -> ApiResponse[BtMcpServerResponse]:
    """更新服务器连接。"""
    server = await service.get_server(session, server_id)
    if server is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.BT_MCP_SERVER_NOT_FOUND,
            detail=f"宝塔 MCP 服务器不存在: {server_id}",
        )
    try:
        server = await service.update_server(session, server, data)
    except ValueError as exc:
        raise BusinessError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            biz_code=ErrorCode.BT_MCP_CONFIG_INVALID,
            detail=str(exc),
        ) from exc
    return ApiResponse(code=0, message="ok", data=_to_response(server))


@router.delete(
    "/{server_id}",
    response_model=ApiResponse[None],
    response_model_exclude_unset=True,
    summary="删除宝塔 MCP 服务器",
)
async def delete_server(
    server_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: BtMcpServerService = Depends(get_bt_mcp_server_service),
) -> ApiResponse[None]:
    """删除服务器连接。"""
    server = await service.get_server(session, server_id)
    if server is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.BT_MCP_SERVER_NOT_FOUND,
            detail=f"宝塔 MCP 服务器不存在: {server_id}",
        )
    await service.delete_server(session, server)
    return ApiResponse(code=0, message="ok", data=None)
