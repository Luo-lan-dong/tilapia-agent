"""LLM Provider 服务单元测试。"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.llm_provider import LLMProvider
from app.schemas.llm_provider import LLMModelCreate, LLMProviderUpdate
from app.services.llm_provider_service import (
    LLMModelNotFoundError,
    LLMProviderConfig,
    LLMProviderService,
)


def _mock_settings(
    api_key: str = "",
    base_url: str = "",
    model: str = "",
) -> MagicMock:
    """构造包含 DeepSeek 环境变量的模拟 Settings。"""
    settings = MagicMock()
    settings.deepseek_api_key = api_key
    settings.deepseek_base_url = base_url
    settings.deepseek_model = model
    return settings


@pytest.fixture
def service() -> LLMProviderService:
    """提供 LLMProviderService 实例。"""
    return LLMProviderService()


async def test_get_active_provider_returns_config(
    db_session,
    service: LLMProviderService,
) -> None:
    """数据库存在活跃记录时返回 LLMProviderConfig。"""
    provider = LLMProvider(
        provider="deepseek",
        api_key="sk-test",
        base_url="https://api.test.com",
        model="deepseek-test",
        is_active=True,
    )
    db_session.add(provider)
    await db_session.commit()

    config = await service.get_active_provider(db_session)

    assert isinstance(config, LLMProviderConfig)
    assert config.provider == "deepseek"
    assert config.api_key == "sk-test"
    assert config.base_url == "https://api.test.com"
    assert config.model == "deepseek-test"


async def test_get_active_provider_not_found(
    db_session,
    service: LLMProviderService,
) -> None:
    """数据库无记录时抛出 ValueError。"""
    with pytest.raises(ValueError, match="配置不存在"):
        await service.get_active_provider(db_session)


async def test_update_provider(
    db_session,
    service: LLMProviderService,
) -> None:
    """更新配置后返回新配置并落库。"""
    provider = LLMProvider(
        provider="deepseek",
        api_key="sk-old",
        base_url="https://api.old.com",
        model="deepseek-old",
        is_active=True,
    )
    db_session.add(provider)
    await db_session.commit()

    data = LLMProviderUpdate(
        api_key="sk-new",
        base_url="https://api.new.com",
        model="deepseek-new",
    )
    config = await service.update_provider(db_session, data)

    assert config.api_key == "sk-new"
    assert config.base_url == "https://api.new.com"
    assert config.model == "deepseek-new"

    refreshed = await db_session.get(LLMProvider, provider.id)
    assert refreshed is not None
    assert refreshed.api_key == "sk-new"


async def test_update_provider_not_found(
    db_session,
    service: LLMProviderService,
) -> None:
    """更新时不存在记录则抛出 ValueError。"""
    data = LLMProviderUpdate(
        api_key="sk-new",
        base_url="https://api.new.com",
        model="deepseek-new",
    )
    with pytest.raises(ValueError, match="配置不存在"):
        await service.update_provider(db_session, data)


async def test_ensure_default_provider_creates_record(
    db_session,
    service: LLMProviderService,
) -> None:
    """表为空时使用环境变量插入默认记录。"""
    mock_settings = _mock_settings(
        api_key="sk-env",
        base_url="https://api.env.com",
        model="deepseek-env",
    )
    with patch(
        "app.services.llm_provider_service.get_settings",
        return_value=mock_settings,
    ):
        await service.ensure_default_provider(db_session)

    config = await service.get_active_provider(db_session)
    assert config.api_key == "sk-env"
    assert config.base_url == "https://api.env.com"
    assert config.model == "deepseek-env"


async def test_ensure_default_provider_idempotent(
    db_session,
    service: LLMProviderService,
) -> None:
    """已存在记录时不会重复插入。"""
    provider = LLMProvider(
        provider="deepseek",
        api_key="sk-existing",
        base_url="https://api.existing.com",
        model="deepseek-existing",
        is_active=True,
    )
    db_session.add(provider)
    await db_session.commit()

    mock_settings = _mock_settings(
        api_key="sk-env",
        base_url="https://api.env.com",
        model="deepseek-env",
    )
    with patch(
        "app.services.llm_provider_service.get_settings",
        return_value=mock_settings,
    ):
        await service.ensure_default_provider(db_session)

    config = await service.get_active_provider(db_session)
    assert config.api_key == "sk-existing"


# ---------------- ADR-030：自定义模型列表管理 ----------------


async def test_get_active_provider_accepts_non_default_provider(
    db_session,
    service: LLMProviderService,
) -> None:
    """激活行 provider 非 deepseek（自定义名称）也可返回（ADR-030）。"""
    provider = LLMProvider(
        provider="硅基流动",
        api_key="sk-sf",
        base_url="https://api.siliconflow.cn/v1",
        model="deepseek-ai/DeepSeek-V3",
        is_active=True,
    )
    db_session.add(provider)
    await db_session.commit()

    config = await service.get_active_provider(db_session)

    assert config.provider == "硅基流动"
    assert config.model == "deepseek-ai/DeepSeek-V3"


async def test_add_model_first_row_auto_activates(
    db_session,
    service: LLMProviderService,
) -> None:
    """表为空时添加首个模型自动设为使用中（U1）。"""
    created = await service.add_model(
        db_session,
        LLMModelCreate(
            provider="DeepSeek",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
            api_key="sk-1",
        ),
    )

    assert created.is_active is True
    rows = await service.list_models(db_session)
    assert len(rows) == 1


async def test_add_model_second_row_not_activated(
    db_session,
    service: LLMProviderService,
) -> None:
    """已有记录时新增模型不抢占使用中状态（U2）。"""
    first = LLMProvider(
        provider="DeepSeek",
        api_key="sk-1",
        base_url="https://a.com",
        model="m1",
        is_active=True,
    )
    db_session.add(first)
    await db_session.commit()

    created = await service.add_model(
        db_session,
        LLMModelCreate(
            provider="OpenRouter",
            model="z-ai/glm-4.6",
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-2",
        ),
    )

    assert created.is_active is False
    refreshed_first = await db_session.get(LLMProvider, first.id)
    assert refreshed_first is not None and refreshed_first.is_active is True


async def test_activate_model_switches_exclusively(
    db_session,
    service: LLMProviderService,
) -> None:
    """激活切换互斥：目标 True、原使用中行失活（U3）。"""
    a = LLMProvider(
        provider="A", api_key="sk-a", base_url="https://a.com", model="m-a", is_active=True
    )
    b = LLMProvider(
        provider="B", api_key="sk-b", base_url="https://b.com", model="m-b", is_active=False
    )
    db_session.add_all([a, b])
    await db_session.commit()

    await service.activate_model(db_session, b.id)

    refreshed_a = await db_session.get(LLMProvider, a.id)
    refreshed_b = await db_session.get(LLMProvider, b.id)
    assert refreshed_a is not None and refreshed_a.is_active is False
    assert refreshed_b is not None and refreshed_b.is_active is True


async def test_activate_model_not_found(
    db_session,
    service: LLMProviderService,
) -> None:
    """激活不存在的模型抛 LLMModelNotFoundError。"""
    with pytest.raises(LLMModelNotFoundError):
        await service.activate_model(db_session, uuid.uuid4())


async def test_delete_model_in_use_succeeds_with_successor(
    db_session,
    service: LLMProviderService,
) -> None:
    """删除使用中的模型：自动激活列表中最早的其他模型接替（ADR-030 修订 U4）。"""
    active = LLMProvider(
        provider="A", api_key="sk-a", base_url="https://a.com", model="m-a", is_active=True
    )
    spare = LLMProvider(
        provider="B", api_key="sk-b", base_url="https://b.com", model="m-b", is_active=False
    )
    db_session.add_all([active, spare])
    await db_session.commit()

    was_active = await service.delete_model(db_session, active.id)

    assert was_active is True
    assert await db_session.get(LLMProvider, active.id) is None
    refreshed_spare = await db_session.get(LLMProvider, spare.id)
    assert refreshed_spare is not None and refreshed_spare.is_active is True


async def test_delete_last_model_leaves_empty(
    db_session,
    service: LLMProviderService,
) -> None:
    """删除唯一的模型（使用中）：列表为空回到未配置状态。"""
    active = LLMProvider(
        provider="A", api_key="sk-a", base_url="https://a.com", model="m-a", is_active=True
    )
    db_session.add(active)
    await db_session.commit()

    was_active = await service.delete_model(db_session, active.id)

    assert was_active is True
    assert await db_session.get(LLMProvider, active.id) is None
    rows = await service.list_models(db_session)
    assert rows == []


async def test_delete_model_not_active_succeeds(
    db_session,
    service: LLMProviderService,
) -> None:
    """非使用中的模型可删除（U5）。"""
    active = LLMProvider(
        provider="A", api_key="sk-a", base_url="https://a.com", model="m-a", is_active=True
    )
    spare = LLMProvider(
        provider="B", api_key="sk-b", base_url="https://b.com", model="m-b", is_active=False
    )
    db_session.add_all([active, spare])
    await db_session.commit()

    await service.delete_model(db_session, spare.id)

    assert await db_session.get(LLMProvider, spare.id) is None
