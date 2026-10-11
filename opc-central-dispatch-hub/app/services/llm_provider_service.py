"""LLM Provider 配置服务。"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import contextvars

from app.core.config import get_settings
from app.models.llm_provider import LLMProvider
from app.schemas.llm_provider import (
    LLMModelCreate,
    LLMModelUpdate,
    LLMProviderUpdate,
    _mask_api_key,
)

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "deepseek"


class LLMModelNotFoundError(Exception):
    """指定的模型配置不存在（ADR-030）。"""


class LLMModelInUseError(Exception):
    """模型配置处于使用中，禁止删除（ADR-030）。"""


def _bind_request_id() -> None:
    """将当前上下文的 request_id 绑定到日志上下文。"""
    request_id = contextvars.get_contextvars().get("request_id")
    if request_id:
        contextvars.bind_contextvars(request_id=request_id)


@dataclass(frozen=True)
class LLMProviderConfig:
    """LLM Provider 运行时配置。"""

    provider: str
    api_key: str
    base_url: str
    model: str
    # 使用中模型是否支持图片输入（ADR-039）；旧调用方默认 False 兼容
    supports_vision: bool = False
    # 模型上下文窗口（token，ADR-041）；占用百分比与压缩触发线的分母。
    # ADR-056：默认值取 128K 作为通用兜底（旧值 64K 明显小于模型实际能力）；
    # 具体模型请按官方口径在模型管理页维护（本仓库当前模型为 1M）
    context_window: int = 128000


class LLMProviderService:
    """LLM Provider 配置管理服务。

    负责从数据库读取使用中配置、自定义模型列表 CRUD（ADR-030）以及首次
    启动时插入默认记录。读取结果不缓存，确保每次调用都能拿到最新配置，
    天然支持热更新。
    """

    def __init__(self, default_provider: str = DEFAULT_PROVIDER) -> None:
        """初始化服务。

        Args:
            default_provider: 默认 provider 类型（仅用于首条记录初始化）。
        """
        self._default_provider = default_provider

    async def get_active_provider(self, session: AsyncSession) -> LLMProviderConfig:
        """获取当前使用中的 LLM 配置（不限 provider 类型，ADR-030）。

        Args:
            session: 异步数据库会话。

        Returns:
            当前使用中的 provider 配置。

        Raises:
            ValueError: 数据库中不存在配置或配置不完整时抛出。
        """
        result = await session.execute(
            select(LLMProvider)
            .where(LLMProvider.is_active.is_(True))
            .order_by(LLMProvider.created_at.asc())
            .limit(1)
        )
        provider = result.scalar_one_or_none()
        if provider is None:
            raise ValueError("LLM Provider 配置不存在，请先完成配置")

        return LLMProviderConfig(
            provider=provider.provider,
            api_key=provider.api_key,
            base_url=provider.base_url,
            model=provider.model,
            supports_vision=bool(provider.supports_vision),
            context_window=int(provider.context_window),
        )

    async def update_provider(
        self,
        session: AsyncSession,
        data: LLMProviderUpdate,
    ) -> LLMProviderConfig:
        """更新默认 LLM Provider 配置。

        Args:
            session: 异步数据库会话。
            data: 更新数据。

        Returns:
            更新后的配置。

        Raises:
            ValueError: 配置记录不存在时抛出。
        """
        result = await session.execute(
            select(LLMProvider)
            .where(LLMProvider.is_active.is_(True))
            .order_by(LLMProvider.created_at.asc())
            .limit(1)
        )
        provider = result.scalar_one_or_none()
        if provider is None:
            raise ValueError("LLM Provider 配置不存在，请先完成配置")

        # 若前端传回脱敏后的 api_key，则保留数据库原值，避免覆盖为 ***。
        if _mask_api_key(provider.api_key) != data.api_key:
            provider.api_key = data.api_key
        provider.base_url = data.base_url
        provider.model = data.model
        await session.commit()
        await session.refresh(provider)

        logger.info(
            "llm_provider_updated",
            extra={
                "event": "llm_provider_updated",
                "provider": provider.provider,
            },
        )
        return LLMProviderConfig(
            provider=provider.provider,
            api_key=provider.api_key,
            base_url=provider.base_url,
            model=provider.model,
            supports_vision=bool(provider.supports_vision),
            context_window=int(provider.context_window),
        )

    async def ensure_default_provider(self, session: AsyncSession) -> None:
        """确保数据库中存在默认 Provider 记录。

        首次启动或表为空时，使用当前环境变量值插入一条 deepseek 记录。
        若环境变量未设置，则对应字段为空字符串，不影响服务启动。

        Args:
            session: 异步数据库会话。
        """
        result = await session.execute(
            select(LLMProvider).where(LLMProvider.provider == self._default_provider)
        )
        if result.scalar_one_or_none() is not None:
            return

        settings = get_settings()
        provider = LLMProvider(
            provider=self._default_provider,
            api_key=settings.deepseek_api_key or "",
            base_url=settings.deepseek_base_url or "",
            model=settings.deepseek_model or "",
            is_active=True,
        )
        session.add(provider)
        await session.commit()
        logger.info(
            "llm_default_provider_initialized",
            extra={
                "event": "llm_default_provider_initialized",
                "provider": self._default_provider,
            },
        )

    # ------------------------------------------------------------------
    # 自定义模型列表管理（ADR-030）
    # ------------------------------------------------------------------

    async def list_models(self, session: AsyncSession) -> list[LLMProvider]:
        """返回全部自定义模型配置（created_at 升序）。

        Args:
            session: 异步数据库会话。

        Returns:
            模型配置行列表（原样返回 ORM 行，脱敏由响应层处理）。
        """
        result = await session.execute(select(LLMProvider).order_by(LLMProvider.created_at.asc()))
        return list(result.scalars().all())

    async def add_model(self, session: AsyncSession, data: LLMModelCreate) -> LLMProvider:
        """添加自定义 OpenAI 兼容模型；列表为空时自动设为使用中。

        Args:
            session: 异步数据库会话。
            data: 模型创建数据。

        Returns:
            新插入的模型配置行。
        """
        existing = await session.execute(select(LLMProvider).limit(1))
        is_first = existing.scalar_one_or_none() is None
        provider = LLMProvider(
            provider=data.provider,
            model=data.model,
            base_url=data.base_url,
            api_key=data.api_key,
            supports_vision=data.supports_vision,
            context_window=data.context_window,
            is_active=is_first,
        )
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
        logger.info(
            "llm_model_added",
            extra={
                "event": "llm_model_added",
                "model_id": str(provider.id),
                "provider": provider.provider,
                "model": provider.model,
                "activated": is_first,
            },
        )
        return provider

    async def activate_model(self, session: AsyncSession, model_id: uuid.UUID) -> LLMProvider:
        """将指定模型设为使用中（原使用中行自动失活，热生效）。

        Args:
            session: 异步数据库会话。
            model_id: 模型配置 ID。

        Returns:
            切换后的模型配置行。

        Raises:
            LLMModelNotFoundError: 指定 ID 不存在时抛出。
        """
        provider = await session.get(LLMProvider, model_id)
        if provider is None:
            raise LLMModelNotFoundError(str(model_id))
        if not provider.is_active:
            await session.execute(
                update(LLMProvider).where(LLMProvider.is_active.is_(True)).values(is_active=False)
            )
            provider.is_active = True
            await session.commit()
            await session.refresh(provider)
        logger.info(
            "llm_model_activated",
            extra={
                "event": "llm_model_activated",
                "model_id": str(model_id),
                "provider": provider.provider,
                "model": provider.model,
            },
        )
        return provider

    async def update_model(
        self, session: AsyncSession, model_id: uuid.UUID, data: LLMModelUpdate
    ) -> LLMProvider:
        """编辑模型元数据（supports_vision / context_window，ADR-041）。

        Args:
            session: 异步数据库会话。
            model_id: 模型配置 ID。
            data: 更新数据（None 字段跳过）。

        Returns:
            更新后的模型配置行。

        Raises:
            LLMModelNotFoundError: 指定 ID 不存在时抛出。
        """
        provider = await session.get(LLMProvider, model_id)
        if provider is None:
            raise LLMModelNotFoundError(str(model_id))
        if data.supports_vision is not None:
            provider.supports_vision = data.supports_vision
        if data.context_window is not None:
            provider.context_window = data.context_window
        await session.commit()
        await session.refresh(provider)
        logger.info(
            "llm_model_updated",
            extra={
                "event": "llm_model_updated",
                "model_id": str(model_id),
                "context_window": provider.context_window,
                "supports_vision": provider.supports_vision,
            },
        )
        return provider

    async def delete_model(self, session: AsyncSession, model_id: uuid.UUID) -> bool:
        """删除模型配置（ADR-030 修订：使用中也可删，删除后自动切换接替者）。

        若删除的是使用中的模型：列表中还有其他模型时自动激活 created_at
        最早的一个，保证始终有可用主模型；列表删空则回到「未配置」状态
        （消费方按既有降级提示处理）。

        Args:
            session: 异步数据库会话。
            model_id: 模型配置 ID。

        Returns:
            被删除的行是否为使用中模型（供提示文案使用）。

        Raises:
            LLMModelNotFoundError: 指定 ID 不存在时抛出。
        """
        provider = await session.get(LLMProvider, model_id)
        if provider is None:
            raise LLMModelNotFoundError(str(model_id))
        was_active = provider.is_active
        await session.delete(provider)
        await session.flush()

        successor: LLMProvider | None = None
        if was_active:
            result = await session.execute(
                select(LLMProvider).order_by(LLMProvider.created_at.asc()).limit(1)
            )
            successor = result.scalar_one_or_none()
            if successor is not None:
                successor.is_active = True
        await session.commit()
        logger.info(
            "llm_model_deleted",
            extra={
                "event": "llm_model_deleted",
                "model_id": str(model_id),
                "was_active": was_active,
                "successor_id": str(successor.id) if successor is not None else None,
            },
        )
        return was_active
