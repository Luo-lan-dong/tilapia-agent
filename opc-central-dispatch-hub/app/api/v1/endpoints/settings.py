"""系统设置相关 API 端点。

注：本文件超过 250 行路由上限（见 docs/standards/coding-standards.md 4.3）。
网络设置 / LLM 配置 / 版本查询等端点结构同构且各自独立，业务逻辑已
下沉 service 层，剩余行数主要为端点元数据与 Schema 声明，故按豁免
场景保留并在此说明。
"""

import logging
import os
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_async_session,
    get_llm_provider_service,
    get_release_service,
    get_system_settings_service,
    get_system_version_service,
)
from app.core.config import get_settings
from app.core.errors import BusinessError, ErrorCode
from app.schemas.common import ApiResponse
from app.schemas.llm_provider import (
    LLMModelCreate,
    LLMModelResponse,
    LLMModelUpdate,
    LLMProviderResponse,
    LLMProviderUpdate,
)
from app.schemas.release import HubUpdateCheckResponse, ReleasesResponse
from app.schemas.skill import (
    AIDispatchSettingsResponse,
    AIDispatchSettingsUpdate,
    AutoWakeSettingsResponse,
    AutoWakeSettingsUpdate,
)
from app.schemas.system_settings import (
    BudgetPolicyResponse,
    BudgetPolicyUpdate,
    NetworkSettingsResponse,
    NetworkSettingsUpdate,
    PermissionPolicyResponse,
    PermissionPolicyUpdate,
)
from app.schemas.system_version import SystemVersionResponse
from app.services import budget_policy_service, permission_policy_service
from app.services.llm_provider_service import (
    LLMModelNotFoundError,
    LLMProviderService,
)
from app.services.release_service import ReleaseService
from app.services.system_settings_service import SystemSettingsService
from app.services.system_version_service import SystemVersionService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["系统设置"])


@router.get(
    "/llm",
    response_model=ApiResponse[LLMProviderResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="获取 LLM 配置",
    description="返回当前活跃 LLM Provider 配置，API Key 已脱敏。",
)
async def get_llm_config(
    session: AsyncSession = Depends(get_async_session),
    service: LLMProviderService = Depends(get_llm_provider_service),
) -> ApiResponse[LLMProviderResponse]:
    """获取当前 LLM 配置。

    Args:
        session: 异步数据库会话。
        service: LLM Provider 服务实例。

    Returns:
        统一响应，data 为当前 LLM 配置。

    Raises:
        BusinessError: 配置不存在时返回 404。
    """
    logger.info(
        "llm_config_get_started",
        extra={"event": "llm_config_get_started"},
    )
    try:
        config = await service.get_active_provider(session)
    except ValueError as exc:
        logger.warning(
            "llm_config_not_found",
            extra={"event": "llm_config_not_found"},
            exc_info=True,
        )
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.LLM_CONFIG_NOT_FOUND,
            detail=str(exc),
        ) from exc

    logger.info(
        "llm_config_get_completed",
        extra={
            "event": "llm_config_get_completed",
            "provider": config.provider,
        },
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=LLMProviderResponse(
            provider=config.provider,
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            is_active=True,
        ),
    )


@router.patch(
    "/llm",
    response_model=ApiResponse[LLMProviderResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="更新 LLM 配置",
    description="更新当前活跃 LLM Provider 配置，保存后立即生效。",
)
async def update_llm_config(
    request: LLMProviderUpdate,
    session: AsyncSession = Depends(get_async_session),
    service: LLMProviderService = Depends(get_llm_provider_service),
) -> ApiResponse[LLMProviderResponse]:
    """更新 LLM 配置。

    Args:
        request: 更新请求。
        session: 异步数据库会话。
        service: LLM Provider 服务实例。

    Returns:
        统一响应，data 为更新后的 LLM 配置。

    Raises:
        BusinessError: 配置不存在时返回 404。
    """
    logger.info(
        "llm_config_update_started",
        extra={"event": "llm_config_update_started"},
    )
    try:
        config = await service.update_provider(session, request)
    except ValueError as exc:
        logger.warning(
            "llm_config_not_found",
            extra={"event": "llm_config_not_found"},
            exc_info=True,
        )
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.LLM_CONFIG_NOT_FOUND,
            detail=str(exc),
        ) from exc

    logger.info(
        "llm_config_updated",
        extra={
            "event": "llm_config_updated",
            "provider": config.provider,
        },
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=LLMProviderResponse(
            provider=config.provider,
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            is_active=True,
        ),
    )


def _model_not_found(model_id: uuid.UUID) -> BusinessError:
    """构造模型配置不存在的 404 业务错误。"""
    return BusinessError(
        status_code=status.HTTP_404_NOT_FOUND,
        biz_code=ErrorCode.LLM_CONFIG_NOT_FOUND,
        detail=f"模型配置不存在：{model_id}",
    )


@router.get(
    "/llm/models",
    response_model=ApiResponse[list[LLMModelResponse]],
    status_code=status.HTTP_200_OK,
    summary="获取自定义模型列表",
    description="返回全部自定义 OpenAI 兼容模型配置，API Key 已脱敏。",
)
async def list_llm_models(
    session: AsyncSession = Depends(get_async_session),
    service: LLMProviderService = Depends(get_llm_provider_service),
) -> ApiResponse[list[LLMModelResponse]]:
    """获取自定义模型列表（ADR-030）。"""
    rows = await service.list_models(session)
    return ApiResponse(
        code=0,
        message="ok",
        data=[
            LLMModelResponse(
                id=row.id,
                provider=row.provider,
                model=row.model,
                base_url=row.base_url,
                api_key=row.api_key,
                is_active=row.is_active,
                supports_vision=row.supports_vision,
                context_window=row.context_window,
            )
            for row in rows
        ],
    )


@router.post(
    "/llm/models",
    response_model=ApiResponse[LLMModelResponse],
    status_code=status.HTTP_201_CREATED,
    summary="添加自定义模型",
    description="添加 OpenAI 兼容模型配置；列表为空时自动设为使用中。",
)
async def add_llm_model(
    request: LLMModelCreate,
    session: AsyncSession = Depends(get_async_session),
    service: LLMProviderService = Depends(get_llm_provider_service),
) -> ApiResponse[LLMModelResponse]:
    """添加自定义模型（ADR-030）。"""
    logger.info(
        "llm_model_add_started",
        extra={"event": "llm_model_add_started", "provider": request.provider},
    )
    row = await service.add_model(session, request)
    return ApiResponse(
        code=0,
        message="ok",
        data=LLMModelResponse(
            id=row.id,
            provider=row.provider,
            model=row.model,
            base_url=row.base_url,
            api_key=row.api_key,
            is_active=row.is_active,
            supports_vision=row.supports_vision,
            context_window=row.context_window,
        ),
    )


@router.patch(
    "/llm/models/{model_id}",
    response_model=ApiResponse[LLMModelResponse],
    status_code=status.HTTP_200_OK,
    summary="编辑模型元数据",
    description="更新 supports_vision / context_window（ADR-041），None 字段跳过。",
)
async def update_llm_model(
    model_id: uuid.UUID,
    request: LLMModelUpdate,
    session: AsyncSession = Depends(get_async_session),
    service: LLMProviderService = Depends(get_llm_provider_service),
) -> ApiResponse[LLMModelResponse]:
    """编辑模型元数据（ADR-041）。"""
    try:
        row = await service.update_model(session, model_id, request)
    except LLMModelNotFoundError as exc:
        raise _model_not_found(model_id) from exc
    return ApiResponse(
        code=0,
        message="ok",
        data=LLMModelResponse(
            id=row.id,
            provider=row.provider,
            model=row.model,
            base_url=row.base_url,
            api_key=row.api_key,
            is_active=row.is_active,
            supports_vision=row.supports_vision,
            context_window=row.context_window,
        ),
    )


@router.put(
    "/llm/models/{model_id}/activate",
    response_model=ApiResponse[LLMModelResponse],
    status_code=status.HTTP_200_OK,
    summary="设为使用中模型",
    description="切换当前使用中模型，原使用中行自动失活；热生效。",
)
async def activate_llm_model(
    model_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: LLMProviderService = Depends(get_llm_provider_service),
) -> ApiResponse[LLMModelResponse]:
    """切换使用中模型（ADR-030）。"""
    try:
        row = await service.activate_model(session, model_id)
    except LLMModelNotFoundError as exc:
        raise _model_not_found(model_id) from exc

    logger.info(
        "llm_model_activate_api",
        extra={"event": "llm_model_activate_api", "model_id": str(model_id)},
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=LLMModelResponse(
            id=row.id,
            provider=row.provider,
            model=row.model,
            base_url=row.base_url,
            api_key=row.api_key,
            is_active=row.is_active,
            supports_vision=row.supports_vision,
            context_window=row.context_window,
        ),
    )


@router.delete(
    "/llm/models/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除自定义模型",
    description="删除模型配置；删除使用中的模型时自动切换到列表中最早的其他模型（ADR-030 修订）。",
)
async def delete_llm_model(
    model_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: LLMProviderService = Depends(get_llm_provider_service),
) -> None:
    """删除自定义模型（ADR-030）。"""
    try:
        await service.delete_model(session, model_id)
    except LLMModelNotFoundError as exc:
        raise _model_not_found(model_id) from exc


def _actual_backend_port() -> int | None:
    """读取后端实际监听端口（launcher 注入的 PORT 环境变量，ADR-031）。

    源码模式直接 uvicorn 启动时未设置 PORT 则返回 None。
    """
    raw = os.environ.get("PORT")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


@router.get(
    "/network",
    response_model=ApiResponse[NetworkSettingsResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="获取网络配置",
    description="返回当前系统网络配置，包括中枢公网地址和前端访问地址。",
)
async def get_network_settings(
    session: AsyncSession = Depends(get_async_session),
    service: SystemSettingsService = Depends(get_system_settings_service),
) -> ApiResponse[NetworkSettingsResponse]:
    """获取当前网络配置。

    Args:
        session: 异步数据库会话。
        service: 系统设置服务实例。

    Returns:
        统一响应，data 为当前网络配置。
    """
    logger.info(
        "network_settings_get_started",
        extra={"event": "network_settings_get_started"},
    )
    config = await service.get_network_settings(session)
    logger.info(
        "network_settings_get_completed",
        extra={
            "event": "network_settings_get_completed",
            "hub_public_url": config.hub_public_url,
            "frontend_url": config.frontend_url,
        },
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=NetworkSettingsResponse(
            hub_public_url=config.hub_public_url,
            frontend_url=config.frontend_url,
            actual_port=_actual_backend_port(),
        ),
    )


@router.patch(
    "/network",
    response_model=ApiResponse[NetworkSettingsResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="更新网络配置",
    description="更新中枢公网地址和前端访问地址，保存后立即生效。",
)
async def update_network_settings(
    request: NetworkSettingsUpdate,
    session: AsyncSession = Depends(get_async_session),
    service: SystemSettingsService = Depends(get_system_settings_service),
) -> ApiResponse[NetworkSettingsResponse]:
    """更新网络配置。

    Args:
        request: 网络配置更新请求。
        session: 异步数据库会话。
        service: 系统设置服务实例。

    Returns:
        统一响应，data 为更新后的网络配置。
    """
    logger.info(
        "network_settings_update_started",
        extra={
            "event": "network_settings_update_started",
            "hub_public_url": request.hub_public_url,
            "frontend_url": request.frontend_url,
        },
    )
    config = await service.update_network_settings(session, request)
    logger.info(
        "network_settings_update_completed",
        extra={
            "event": "network_settings_update_completed",
            "hub_public_url": config.hub_public_url,
            "frontend_url": config.frontend_url,
        },
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=NetworkSettingsResponse(
            hub_public_url=config.hub_public_url,
            frontend_url=config.frontend_url,
            actual_port=_actual_backend_port(),
        ),
    )


@router.get(
    "/versions",
    response_model=ApiResponse[list[SystemVersionResponse]],
    status_code=status.HTTP_200_OK,
    summary="获取系统版本列表",
    description="返回所有系统版本记录，按发布日期倒序排列，含当前版本标记。",
)
async def list_system_versions(
    session: AsyncSession = Depends(get_async_session),
    service: SystemVersionService = Depends(get_system_version_service),
) -> ApiResponse[list[SystemVersionResponse]]:
    """获取系统版本列表。

    Args:
        session: 异步数据库会话。
        service: 系统版本服务实例。

    Returns:
        统一响应，data 为版本记录列表（按发布日期倒序）。
    """
    logger.info(
        "version_list_get_started",
        extra={"event": "version_list_get_started"},
    )
    versions = await service.list_versions(session)
    logger.info(
        "version_list_get_completed",
        extra={"event": "version_list_get_completed", "count": len(versions)},
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=[SystemVersionResponse.model_validate(v) for v in versions],
    )


@router.get(
    "/releases",
    response_model=ApiResponse[ReleasesResponse],
    status_code=status.HTTP_200_OK,
    summary="获取产品版本发布列表（按产品线分组）",
    description="从管理后台拉取全部产品线版本记录并按产品分组（ADR-026）；服务不可达时返回 error 字段。",
)
async def list_releases(
    service: ReleaseService = Depends(get_release_service),
) -> ApiResponse[ReleasesResponse]:
    """获取按产品线分组的版本发布列表（更新日志数据源，完全云端化）。"""
    logger.info(
        "release_list_get_started",
        extra={"event": "release_list_get_started"},
    )
    releases, error = await service.fetch_releases()
    if releases is None:
        logger.warning(
            "release_list_get_failed",
            extra={"event": "release_list_get_failed", "error": error},
        )
        return ApiResponse(
            code=0,
            message="ok",
            data=ReleasesResponse(products=[], error=error),
        )
    groups = service.group_releases(releases)
    logger.info(
        "release_list_get_completed",
        extra={
            "event": "release_list_get_completed",
            "products": [g.product for g in groups],
        },
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=ReleasesResponse(
            products=[
                {
                    "product": g.product,
                    "label": g.label,
                    "releases": [
                        {
                            "product": r.product,
                            "version": r.version,
                            "title": r.title,
                            "content": r.content,
                            "release_date": r.release_date,
                            "download_url": r.download_url,
                        }
                        for r in g.releases
                    ],
                }
                for g in groups
            ],
        ),
    )


@router.get(
    "/ai-dispatch",
    response_model=ApiResponse[AIDispatchSettingsResponse],
    status_code=status.HTTP_200_OK,
    summary="获取 AI 智能调度设置",
    description="返回 AI 智能调度开关状态与决策参数（ADR-033）。",
)
async def get_ai_dispatch_settings(
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[AIDispatchSettingsResponse]:
    """读取 AI 智能调度开关（system_settings 优先，env 兜底）。"""
    from app.services.dispatch_decision_service import is_ai_dispatch_enabled

    logger.info("ai_dispatch_get_started", extra={"event": "ai_dispatch_get_started"})
    enabled = await is_ai_dispatch_enabled(session)
    settings = get_settings()
    logger.info(
        "ai_dispatch_get_completed",
        extra={"event": "ai_dispatch_get_completed", "enabled": enabled},
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=AIDispatchSettingsResponse(
            enabled=enabled,
            timeout_seconds=settings.ai_dispatch_timeout_seconds,
            max_tool_rounds=settings.orchestrator_max_tool_rounds,
        ),
    )


@router.put(
    "/ai-dispatch",
    response_model=ApiResponse[AIDispatchSettingsResponse],
    status_code=status.HTTP_200_OK,
    summary="更新 AI 智能调度开关",
    description="开启/关闭自动调度的 AI 决策；下一轮调度周期热生效（ADR-033）。",
)
async def update_ai_dispatch_settings(
    request: AIDispatchSettingsUpdate,
    session: AsyncSession = Depends(get_async_session),
    settings_service: SystemSettingsService = Depends(get_system_settings_service),
) -> ApiResponse[AIDispatchSettingsResponse]:
    """更新 AI 智能调度开关。"""
    logger.info(
        "ai_dispatch_update_started",
        extra={"event": "ai_dispatch_update_started", "enabled": request.enabled},
    )
    enabled = await settings_service.set_ai_dispatch_enabled(session, request.enabled)
    settings = get_settings()
    logger.info(
        "ai_dispatch_update_completed",
        extra={"event": "ai_dispatch_update_completed", "enabled": enabled},
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=AIDispatchSettingsResponse(
            enabled=enabled,
            timeout_seconds=settings.ai_dispatch_timeout_seconds,
            max_tool_rounds=settings.orchestrator_max_tool_rounds,
        ),
    )


@router.get(
    "/auto-wake",
    response_model=ApiResponse[AutoWakeSettingsResponse],
    status_code=status.HTTP_200_OK,
    summary="获取任务终态自动唤醒编排设置",
    description="返回任务终态自动唤醒编排开关状态（ADR-066）：completed/failed 后自动核对交付物、推进待办并汇报。",
)
async def get_auto_wake_settings(
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[AutoWakeSettingsResponse]:
    """读取任务终态自动唤醒编排开关（system_settings 优先，env 兜底）。"""
    from app.services.system_settings_service import is_orchestrator_auto_wake_enabled

    logger.info("auto_wake_get_started", extra={"event": "auto_wake_get_started"})
    enabled = await is_orchestrator_auto_wake_enabled(session)
    logger.info(
        "auto_wake_get_completed",
        extra={"event": "auto_wake_get_completed", "enabled": enabled},
    )
    return ApiResponse(code=0, message="ok", data=AutoWakeSettingsResponse(enabled=enabled))


@router.put(
    "/auto-wake",
    response_model=ApiResponse[AutoWakeSettingsResponse],
    status_code=status.HTTP_200_OK,
    summary="更新任务终态自动唤醒编排开关",
    description="开启/关闭任务终态自动唤醒编排；下一个任务终态热生效（ADR-066）。",
)
async def update_auto_wake_settings(
    request: AutoWakeSettingsUpdate,
    session: AsyncSession = Depends(get_async_session),
    settings_service: SystemSettingsService = Depends(get_system_settings_service),
) -> ApiResponse[AutoWakeSettingsResponse]:
    """更新任务终态自动唤醒编排开关。"""
    logger.info(
        "auto_wake_update_started",
        extra={"event": "auto_wake_update_started", "enabled": request.enabled},
    )
    enabled = await settings_service.set_orchestrator_auto_wake_enabled(
        session, request.enabled
    )
    logger.info(
        "auto_wake_update_completed",
        extra={"event": "auto_wake_update_completed", "enabled": enabled},
    )
    return ApiResponse(code=0, message="ok", data=AutoWakeSettingsResponse(enabled=enabled))


@router.get(
    "/permission-policy",
    response_model=ApiResponse[PermissionPolicyResponse],
    status_code=status.HTTP_200_OK,
    summary="获取子 Agent 权限策略",
    description="返回全局单条权限策略（ADR-061 M2b）；未配置时返回最严默认值（unattended + 空白名单）。",
)
async def get_permission_policy(
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[PermissionPolicyResponse]:
    """读取全局权限策略（缺省即最严，不放宽任何权限）。"""
    logger.info("permission_policy_get_started", extra={"event": "permission_policy_get_started"})
    policy = await permission_policy_service.load_policy(session)
    logger.info(
        "permission_policy_get_completed",
        extra={
            "event": "permission_policy_get_completed",
            "mode": policy["mode"],
            "allow_count": len(policy["allow"]),
        },
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=PermissionPolicyResponse.model_validate(policy),
    )


@router.put(
    "/permission-policy",
    response_model=ApiResponse[PermissionPolicyResponse],
    status_code=status.HTTP_200_OK,
    summary="更新子 Agent 权限策略",
    description=(
        "更新全局权限策略（ADR-061 M2b）。仅表达 allow 白名单；"
        "blanket allow（action=* 且 resource=*）会被服务层丢弃并告警。下次派发时下发给节点。"
    ),
)
async def update_permission_policy(
    request: PermissionPolicyUpdate,
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[PermissionPolicyResponse]:
    """更新全局权限策略（保存后下一次派发生效）。"""
    logger.info(
        "permission_policy_update_started",
        extra={
            "event": "permission_policy_update_started",
            "mode": request.mode,
            "allow_count": len(request.allow),
        },
    )
    policy = await permission_policy_service.save_policy(session, request.model_dump())
    logger.info(
        "permission_policy_updated",
        extra={
            "event": "permission_policy_updated",
            "mode": policy["mode"],
            "allow_count": len(policy["allow"]),
        },
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=PermissionPolicyResponse.model_validate(policy),
    )


@router.get(
    "/budget-policy",
    response_model=ApiResponse[BudgetPolicyResponse],
    status_code=status.HTTP_200_OK,
    summary="获取任务执行预算",
    description="返回全局预算（ADR-061 M5b）；未配置时返回内置默认（60 轮 / 200 万 tokens / 80%）。",
)
async def get_budget_policy(
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[BudgetPolicyResponse]:
    """读取全局执行预算。"""
    logger.info("budget_policy_get_started", extra={"event": "budget_policy_get_started"})
    policy = await budget_policy_service.load_policy(session)
    return ApiResponse(code=0, message="ok", data=BudgetPolicyResponse.model_validate(policy))


@router.put(
    "/budget-policy",
    response_model=ApiResponse[BudgetPolicyResponse],
    status_code=status.HTTP_200_OK,
    summary="更新任务执行预算",
    description=(
        "更新全局执行预算（ADR-061 M5b）：80% 发收尾指令、100% 强制停止；"
        "度量 steps 与 tokens 双维度、任一超限即触发。任务级可在创建任务时覆盖。"
    ),
)
async def update_budget_policy(
    request: BudgetPolicyUpdate,
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[BudgetPolicyResponse]:
    """更新全局执行预算（下一轮调度扫描生效）。"""
    logger.info(
        "budget_policy_update_started",
        extra={
            "event": "budget_policy_update_started",
            "max_steps": request.max_steps,
            "max_tokens": request.max_tokens,
        },
    )
    policy = await budget_policy_service.save_policy(session, request.model_dump())
    return ApiResponse(code=0, message="ok", data=BudgetPolicyResponse.model_validate(policy))


@router.post(
    "/check-hub-update",
    response_model=ApiResponse[HubUpdateCheckResponse],
    status_code=status.HTTP_200_OK,
    summary="检查中枢更新",
    description="比对管理后台 hub 产品线最新版本与本地当前版本（ADR-026），有新版时返回更新日志与安装包下载链接。",
)
async def check_hub_update(
    session: AsyncSession = Depends(get_async_session),
    version_service: SystemVersionService = Depends(get_system_version_service),
    release_service: ReleaseService = Depends(get_release_service),
) -> ApiResponse[HubUpdateCheckResponse]:
    """检查中枢自身更新（检查+提示+下载引导，安装由用户手动完成）。"""
    logger.info(
        "hub_update_check_endpoint_started",
        extra={"event": "hub_update_check_endpoint_started"},
    )
    versions = await version_service.list_versions(session)
    current = next((v.version for v in versions if v.is_current), None)
    result = await release_service.check_hub_update(current)
    logger.info(
        "hub_update_check_endpoint_completed",
        extra={
            "event": "hub_update_check_endpoint_completed",
            "status": result.status,
        },
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=HubUpdateCheckResponse(
            status=result.status,
            latest_version=result.latest_version,
            current_version=result.current_version,
            title=result.title,
            content=result.content,
            download_url=result.download_url,
            message=result.message,
        ),
    )
