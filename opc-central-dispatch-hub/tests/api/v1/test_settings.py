"""系统设置 API 测试。"""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.models.base import AsyncSessionLocal
from app.models.llm_provider import LLMProvider
from app.models.system_settings import SystemSettings


async def _create_real_session() -> AsyncSession:
    """创建直接写入真实数据库的会话，供端到端 API 测试使用。"""
    engine = create_async_engine(get_settings().database_url, echo=False)
    return AsyncSessionLocal(bind=engine)


@pytest.fixture
async def default_provider() -> LLMProvider:
    """提供一条默认 DeepSeek 记录并写入真实数据库，测试结束后清理。"""
    provider = LLMProvider(
        id=uuid.uuid4(),
        provider="deepseek",
        api_key="sk-test",
        base_url="https://api.test.com",
        model="deepseek-test",
        is_active=True,
    )
    session = await _create_real_session()
    async with session:
        await session.execute(delete(LLMProvider))
        session.add(provider)
        await session.commit()

    yield provider

    session = await _create_real_session()
    async with session:
        await session.execute(delete(LLMProvider))
        await session.commit()


async def test_get_llm_config(
    ws_client,
    default_provider: LLMProvider,
) -> None:
    """GET /settings/llm 返回当前配置。"""
    response = ws_client.get("/api/v1/settings/llm")

    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    assert data["data"]["provider"] == "deepseek"
    assert data["data"]["api_key"] == "***"
    assert data["data"]["base_url"] == "https://api.test.com"
    assert data["data"]["model"] == "deepseek-test"
    assert data["data"]["is_active"] is True


async def test_get_llm_config_not_found(ws_client) -> None:
    """数据库无记录时返回 404。"""
    session = await _create_real_session()
    async with session:
        await session.execute(delete(LLMProvider))
        await session.commit()

    response = ws_client.get("/api/v1/settings/llm")

    assert response.status_code == 404
    data = response.json()
    assert data["code"] == 203001


async def test_update_llm_config(
    ws_client,
    default_provider: LLMProvider,
) -> None:
    """PATCH /settings/llm 更新配置并立即生效。"""
    response = ws_client.patch(
        "/api/v1/settings/llm",
        json={
            "api_key": "sk-new",
            "base_url": "https://api.new.com",
            "model": "deepseek-new",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    assert data["data"]["api_key"] == "***"
    assert data["data"]["base_url"] == "https://api.new.com"
    assert data["data"]["model"] == "deepseek-new"


async def test_update_llm_config_with_masked_key_preserves_original(
    ws_client,
    default_provider: LLMProvider,
) -> None:
    """PATCH 传回脱敏 api_key 时不应覆盖数据库原值。"""
    response = ws_client.patch(
        "/api/v1/settings/llm",
        json={
            "api_key": "***",
            "base_url": "https://api.preserved.com",
            "model": "deepseek-preserved",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    assert data["data"]["api_key"] == "***"
    assert data["data"]["base_url"] == "https://api.preserved.com"
    assert data["data"]["model"] == "deepseek-preserved"

    # 再次获取，确认原 api_key 未被覆盖
    response = ws_client.get("/api/v1/settings/llm")
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["api_key"] == "***"


async def test_update_llm_config_validation_error(
    ws_client,
    default_provider: LLMProvider,
) -> None:
    """缺少必填字段时返回 422。"""
    response = ws_client.patch(
        "/api/v1/settings/llm",
        json={"api_key": "sk-new"},
    )

    assert response.status_code == 422
    data = response.json()
    # 参数校验失败统一使用通用域错误码 100001（api-standards §3）
    assert data["code"] == 100001


async def test_get_network_settings_reports_actual_port(
    ws_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /settings/network 返回后端实际监听端口（ADR-031）。"""
    monkeypatch.setenv("PORT", "8023")

    response = ws_client.get("/api/v1/settings/network")

    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    assert data["data"]["actual_port"] == 8023


async def test_get_network_settings_actual_port_none_without_env(
    ws_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未设置 PORT（源码模式手动启动）时 actual_port 返回 null。"""
    monkeypatch.delenv("PORT", raising=False)

    response = ws_client.get("/api/v1/settings/network")

    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    assert data["data"]["actual_port"] is None


async def _load_settings_row(session: AsyncSession) -> SystemSettings | None:
    """读取单行系统设置（测试辅助）。"""
    result = await session.execute(
        select(SystemSettings).order_by(SystemSettings.created_at.asc()).limit(1)
    )
    return result.scalar_one_or_none()


@pytest.fixture
async def restore_permission_policy() -> AsyncIterator[None]:
    """快照并在用例结束后恢复 ``permission_policy``（真实库，避免污染开发数据）。"""
    session = await _create_real_session()
    async with session:
        row = await _load_settings_row(session)
        original = row.permission_policy if row is not None else None
    yield
    session = await _create_real_session()
    async with session:
        row = await _load_settings_row(session)
        if row is not None:
            row.permission_policy = original
            await session.commit()


async def test_get_permission_policy_defaults_to_strictest(
    ws_client, restore_permission_policy: None
) -> None:
    """UT-M2b-9（ADR-061 M2b）：未配置策略时返回最严默认值（unattended + 空白名单）。"""
    session = await _create_real_session()
    async with session:
        row = await _load_settings_row(session)
        if row is not None:
            row.permission_policy = None
            await session.commit()

    response = ws_client.get("/api/v1/settings/permission-policy")

    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    assert data["data"]["mode"] == "unattended"
    assert data["data"]["allow"] == []


async def test_update_and_get_permission_policy(
    ws_client, restore_permission_policy: None
) -> None:
    """UT-M2b-10：PUT 保存白名单，GET 回读一致（下发内容可断言）。"""
    payload = {
        "mode": "unattended",
        "allow": [
            {"action": "read", "resource": "/usr/include/**"},
            {"action": "bash", "resource": "git *"},
        ],
    }

    put_response = ws_client.put("/api/v1/settings/permission-policy", json=payload)
    get_response = ws_client.get("/api/v1/settings/permission-policy")

    assert put_response.status_code == 200
    assert put_response.json()["data"] == payload
    assert get_response.json()["data"] == payload


async def test_update_permission_policy_drops_blanket_allow(
    ws_client, restore_permission_policy: None
) -> None:
    """UT-M2b-11（安全红线）：blanket allow（``*``/``*``）必须被服务层丢弃。"""
    response = ws_client.put(
        "/api/v1/settings/permission-policy",
        json={
            "mode": "unattended",
            "allow": [
                {"action": "*", "resource": "*"},
                {"action": "read", "resource": "/opt/**"},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["allow"] == [{"action": "read", "resource": "/opt/**"}]


async def test_update_permission_policy_rejects_unknown_field(
    ws_client, restore_permission_policy: None
) -> None:
    """UT-M2b-12：请求体额外字段一律拒绝（``extra="forbid"``）。"""
    response = ws_client.put(
        "/api/v1/settings/permission-policy",
        json={"mode": "unattended", "allow": [], "permission": {"*": "allow"}},
    )

    assert response.status_code == 422
