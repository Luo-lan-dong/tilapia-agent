"""FastAPI 依赖注入组件。"""

from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.config import get_settings as _get_settings
from app.models.base import AsyncSessionLocal
from app.services.bt_mcp_server_service import BtMcpServerService
from app.services.experience_service import ExperienceService
from app.services.llm_provider_service import LLMProviderService
from app.services.orchestrator_service import OrchestratorService
from app.services.release_service import ReleaseService
from app.services.resource_sync_service import ResourceSyncService
from app.services.skill_service import SkillService
from app.services.standard_doc_service import StandardDocService
from app.services.system_settings_service import SystemSettingsService
from app.services.system_version_service import SystemVersionService


def get_release_service() -> ReleaseService:
    """获取产品版本发布服务依赖（ADR-026）。"""
    return ReleaseService()


def get_resource_sync_service() -> ResourceSyncService:
    """获取 Agent 资源同步服务依赖（缓存目录来自配置）。"""
    return ResourceSyncService(cache_dir=Path(_get_settings().resource_cache_dir))


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """获取异步数据库会话依赖。

    Yields:
        异步 SQLAlchemy 会话，请求结束后自动关闭。
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def get_settings() -> Settings:
    """获取应用配置依赖。

    Returns:
        当前环境对应的 Pydantic Settings 实例。
    """
    return _get_settings()


def get_llm_provider_service() -> LLMProviderService:
    """获取 LLM Provider 服务实例依赖。

    每次请求新建实例，保持简单且无共享状态。

    Returns:
        LLMProviderService 实例。
    """
    return LLMProviderService()


def get_system_settings_service() -> SystemSettingsService:
    """获取系统设置服务实例依赖。

    Returns:
        SystemSettingsService 实例。
    """
    return SystemSettingsService()


def get_system_version_service() -> SystemVersionService:
    """获取系统版本服务实例依赖。

    Returns:
        SystemVersionService 实例。
    """
    return SystemVersionService()


def get_skill_service() -> SkillService:
    """获取技能管理服务实例依赖（ADR-033）。"""
    return SkillService()


def get_standard_doc_service() -> StandardDocService:
    """获取开发规则文档服务实例依赖（ADR-048）。"""
    return StandardDocService()


def get_bt_mcp_server_service() -> BtMcpServerService:
    """获取宝塔 MCP 服务器管理服务实例依赖（ADR-049）。"""
    return BtMcpServerService()


def get_experience_service() -> ExperienceService:
    """获取经验记忆服务实例依赖（ADR-050）。"""
    return ExperienceService()


def get_orchestrator_service(
    session: AsyncSession = Depends(get_async_session),
) -> OrchestratorService:
    """获取统一对话链路服务实例依赖（ADR-033）。"""
    return OrchestratorService(session)
