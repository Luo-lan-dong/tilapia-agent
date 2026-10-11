"""Agent 资源管理 API 端点（ADR-022 / ADR-025）。

路径前缀：/settings/resources（挂载于 /api/v1/settings 之下）。
"""

import logging

from fastapi import APIRouter, Depends, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_async_session,
    get_resource_sync_service,
    get_system_settings_service,
)
from app.core.errors import BusinessError, ErrorCode
from app.schemas.common import ApiResponse
from app.schemas.resource_settings import (
    ResourceStatusResponse,
    ResourceUpdateRequest,
    ResourceUpdateResponse,
)
from app.services.resource_sync_service import (
    STATUS_DISABLED,
    STATUS_FAILED,
    STATUS_OFFLINE,
    STATUS_SYNCING,
    ResourceSyncService,
    SyncResult,
)
from app.services.system_settings_service import SystemSettingsService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["资源管理"])


async def _effective_manifest_url(
    sync_service: ResourceSyncService,
    settings_service: SystemSettingsService,
    session: AsyncSession,
) -> str | None:
    """解析生效的仓库地址（ADR-025）。

    先从服务端配置接口拉取（地址/Token 来自配置文件，用户零配置）；
    失败回退 state 缓存 / 数据库缓存 / 内置默认。
    """
    resolved = await sync_service.resolve_manifest_url()
    if resolved:
        # 拉到最新地址 → 持久化为运行期缓存（ADR-025）
        await settings_service.set_agent_resource_manifest_url(session, resolved)
        return resolved
    return await settings_service.get_agent_resource_manifest_url(session)


def _raise_if_sync_failed(result: SyncResult) -> None:
    """同步结果为失败类状态时抛出业务异常（api-standards §2.3）。

    - ``syncing``：并发冲突 → 409
    - ``failed`` / ``disabled`` / ``offline``：操作未达成功 → 422
    """
    if result.status == STATUS_SYNCING:
        raise BusinessError(
            status_code=status.HTTP_409_CONFLICT,
            biz_code=ErrorCode.RESOURCE_SYNC_FAILED,
            detail=result.message or "已有同步任务进行中",
        )
    if result.status in (STATUS_FAILED, STATUS_DISABLED, STATUS_OFFLINE):
        raise BusinessError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            biz_code=ErrorCode.RESOURCE_SYNC_FAILED,
            detail=result.message or "资源同步失败",
        )


@router.get(
    "",
    response_model=ApiResponse[ResourceStatusResponse],
    response_model_exclude_unset=True,
    summary="获取资源同步状态",
)
async def get_resource_status(
    sync_service: ResourceSyncService = Depends(get_resource_sync_service),
) -> ApiResponse[ResourceStatusResponse]:
    """返回当前缓存状态、revision、资源差异与按类型分组（不触发网络请求）。"""
    return ApiResponse(code=0, message="ok", data=ResourceStatusResponse(**sync_service.get_status_view()))


@router.post(
    "/check",
    response_model=ApiResponse[ResourceStatusResponse],
    summary="立即检查云端更新（不下载）",
)
async def check_resources(
    sync_service: ResourceSyncService = Depends(get_resource_sync_service),
    settings_service: SystemSettingsService = Depends(get_system_settings_service),
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[ResourceStatusResponse]:
    """触发一次检查：地址优先从服务端配置接口获取（失败回退缓存），仅比对不下载。"""
    url = await _effective_manifest_url(sync_service, settings_service, session)
    result = await sync_service.check(url)
    view = sync_service.get_status_view()
    view["message"] = result.message
    return ApiResponse(code=0, message="ok", data=ResourceStatusResponse(**view))


@router.post(
    "/update",
    response_model=ApiResponse[ResourceUpdateResponse],
    summary="应用更新（type=按 agent 类型；keys=按条目；缺省=全部过时资源）",
)
async def update_resources(
    payload: ResourceUpdateRequest | None = None,
    sync_service: ResourceSyncService = Depends(get_resource_sync_service),
    settings_service: SystemSettingsService = Depends(get_system_settings_service),
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[ResourceUpdateResponse]:
    """下载并落盘资源；type 与 keys 均缺省时更新全部过时资源。"""
    payload = payload or ResourceUpdateRequest()
    keys = payload.keys
    if payload.type:
        type_keys = sync_service.keys_for_type(payload.type)
        if not type_keys:
            raise BusinessError(
                status_code=status.HTTP_404_NOT_FOUND,
                biz_code=ErrorCode.RESOURCE_NOT_FOUND,
                detail=f"未找到类型 {payload.type} 的资源条目",
            )
        # 类型模式：更新该类型全部条目（脚本随整体包自动更新，ADR-025）
        keys = type_keys if keys is None else [k for k in keys if k in type_keys]
    url = await _effective_manifest_url(sync_service, settings_service, session)
    result = await sync_service.apply(url, keys)
    _raise_if_sync_failed(result)
    return ApiResponse(
        code=0,
        message="ok",
        data=ResourceUpdateResponse(
            status=result.status,
            updated_keys=result.updated_keys,
            message=result.message,
        ),
    )


@router.post(
    "/import",
    response_model=ApiResponse[ResourceUpdateResponse],
    summary="导入资源包 zip（云端仓库同构）或节点整体包 tar.gz（离线兜底）",
)
async def import_resources(
    file: UploadFile,
    sync_service: ResourceSyncService = Depends(get_resource_sync_service),
) -> ApiResponse[ResourceUpdateResponse]:
    """按上传文件名分流：``.tar.gz``/``.tgz`` 为节点整体包导入，其余按 zip 资源包导入。"""
    data = await file.read()
    filename = file.filename or ""
    if filename.lower().endswith((".tar.gz", ".tgz")):
        result = await sync_service.import_tarball(data, filename)
    else:
        result = await sync_service.import_zip(data)
    _raise_if_sync_failed(result)
    return ApiResponse(
        code=0,
        message="ok",
        data=ResourceUpdateResponse(
            status=result.status,
            updated_keys=result.updated_keys,
            message=result.message,
        ),
    )
