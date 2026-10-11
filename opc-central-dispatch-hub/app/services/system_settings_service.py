"""系统设置服务层。"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import contextvars

from app.core.config import get_settings
from app.models.system_settings import SystemSettings
from app.schemas.system_settings import NetworkSettingsUpdate

logger = logging.getLogger(__name__)


def _bind_request_id() -> None:
    """将当前上下文的 request_id 绑定到日志上下文。"""
    request_id = contextvars.get_contextvars().get("request_id")
    if request_id:
        contextvars.bind_contextvars(request_id=request_id)


class SystemSettingsService:
    """系统设置管理服务。

    负责从数据库读取/更新全局运行时配置，读取结果不缓存，天然支持热更新。
    当前仅维护单行记录，用于存储网络相关配置。
    """

    async def _get_or_create_settings(
        self,
        session: AsyncSession,
    ) -> SystemSettings:
        """获取系统设置记录，不存在时自动创建空记录。

        Args:
            session: 异步数据库会话。

        Returns:
            系统设置数据库模型实例。
        """
        result = await session.execute(
            select(SystemSettings).order_by(SystemSettings.created_at.asc()).limit(1)
        )
        settings = result.scalar_one_or_none()
        if settings is None:
            settings = SystemSettings()
            session.add(settings)
            await session.commit()
            await session.refresh(settings)
            logger.info(
                "system_settings_initialized",
                extra={"event": "system_settings_initialized"},
            )
        return settings

    async def get_network_settings(self, session: AsyncSession) -> SystemSettings:
        """获取当前网络配置。

        Args:
            session: 异步数据库会话。

        Returns:
            网络配置数据库模型实例。
        """
        settings = await self._get_or_create_settings(session)

        logger.debug(
            "network_settings_queried",
            extra={
                "event": "network_settings_queried",
                "hub_public_url": settings.hub_public_url,
                "frontend_url": settings.frontend_url,
            },
        )
        return settings

    async def update_network_settings(
        self,
        session: AsyncSession,
        data: NetworkSettingsUpdate,
    ) -> SystemSettings:
        """更新网络配置。

        Args:
            session: 异步数据库会话。
            data: 网络配置更新数据。

        Returns:
            更新后的系统设置数据库模型实例。
        """
        settings = await self._get_or_create_settings(session)

        if data.hub_public_url is not None:
            settings.hub_public_url = data.hub_public_url.strip() or None
        if data.frontend_url is not None:
            settings.frontend_url = data.frontend_url.strip() or None

        await session.commit()
        await session.refresh(settings)

        logger.info(
            "network_settings_updated",
            extra={
                "event": "network_settings_updated",
                "hub_public_url": settings.hub_public_url,
                "frontend_url": settings.frontend_url,
            },
        )
        return settings

    async def get_agent_resource_manifest_url(self, session: AsyncSession) -> str:
        """获取 Agent 资源云端仓库 manifest URL（ADR-022）。

        数据库已配置的值优先；未配置（NULL）时回退内置默认值
        （``AGENT_RESOURCE_MANIFEST_URL``，开箱即用）。
        """
        settings = await self._get_or_create_settings(session)
        url = settings.agent_resource_manifest_url
        if url:
            return url
        default_url = get_settings().agent_resource_manifest_url
        logger.debug(
            "agent_resource_manifest_url_fallback_default",
            extra={
                "event": "agent_resource_manifest_url_fallback_default",
                "url": default_url,
            },
        )
        return default_url

    async def set_agent_resource_manifest_url(
        self,
        session: AsyncSession,
        url: str,
    ) -> str:
        """设置 Agent 资源云端仓库 manifest URL（ADR-025 起为运行期缓存），返回生效值。"""
        settings = await self._get_or_create_settings(session)
        normalized = url.strip() or None
        settings.agent_resource_manifest_url = normalized
        await session.commit()
        await session.refresh(settings)
        effective = normalized or get_settings().agent_resource_manifest_url
        logger.info(
            "agent_resource_manifest_url_updated",
            extra={
                "event": "agent_resource_manifest_url_updated",
                "url": normalized,
                "effective_url": effective,
            },
        )
        return effective

    async def set_ai_dispatch_enabled(
        self,
        session: AsyncSession,
        enabled: bool,
    ) -> bool:
        """设置 AI 智能调度开关（ADR-033），下一轮调度周期热生效。"""
        settings = await self._get_or_create_settings(session)
        settings.ai_dispatch_enabled = enabled
        await session.commit()
        logger.info(
            "ai_dispatch_enabled_updated",
            extra={"event": "ai_dispatch_enabled_updated", "enabled": enabled},
        )
        return enabled

    async def set_orchestrator_auto_wake_enabled(
        self,
        session: AsyncSession,
        enabled: bool,
    ) -> bool:
        """设置任务终态自动唤醒编排开关（ADR-066），下一个任务终态热生效。"""
        settings = await self._get_or_create_settings(session)
        settings.orchestrator_auto_wake_enabled = enabled
        await session.commit()
        logger.info(
            "orchestrator_auto_wake_enabled_updated",
            extra={"event": "orchestrator_auto_wake_enabled_updated", "enabled": enabled},
        )
        return enabled


async def is_orchestrator_auto_wake_enabled(session: AsyncSession) -> bool:
    """读取任务终态自动唤醒编排开关（ADR-066）：system_settings 优先，env 兜底。"""
    from app.core.config import get_settings

    result = await session.execute(
        select(SystemSettings).order_by(SystemSettings.created_at.asc()).limit(1)
    )
    settings = result.scalar_one_or_none()
    if settings is not None and settings.orchestrator_auto_wake_enabled is not None:
        return settings.orchestrator_auto_wake_enabled
    return get_settings().orchestrator_auto_wake_enabled
