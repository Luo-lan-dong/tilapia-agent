"""启动时 hub_public_url 端口校准测试（ADR-031）。

桌面模式端口避让后实际端口可能与 Hub URL 端口不一致，lifespan 启动时
自动把 URL 端口替换为实际端口；本模块验证各分支行为（savepoint 回滚，
不污染开发库）。

校准仅在 APP_ENV=desktop 生效（源码模式手动设 PORT 的临时实例不得
静默改写持久化配置，2026-09-12 冒烟验证污染事故后的收紧）。
"""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.main import _reconcile_hub_public_url_port
from app.models.system_settings import SystemSettings


@pytest.fixture
def desktop_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """伪造 APP_ENV=desktop（get_settings 有 lru 缓存，直接替换引用）。"""
    monkeypatch.setattr(
        "app.main.get_settings", lambda: SimpleNamespace(app_env="desktop")
    )


async def _get_or_create_settings_row(session, hub_public_url: str | None) -> SystemSettings:
    """取当前单行设置记录，无则创建（均在 savepoint 内，测试后回滚）。"""
    result = await session.execute(select(SystemSettings).limit(1))
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemSettings(id=uuid.uuid4(), hub_public_url=hub_public_url)
        session.add(row)
        await session.flush()
    else:
        row.hub_public_url = hub_public_url
        await session.flush()
    return row


async def test_reconcile_replaces_url_port_with_actual_port(
    db_session, monkeypatch, desktop_env
):
    """PORT 环境变量端口与 URL 端口不一致时，URL 端口应替换为实际端口。"""
    row = await _get_or_create_settings_row(db_session, "http://10.0.0.2:8000")
    monkeypatch.setenv("PORT", "8011")

    await _reconcile_hub_public_url_port(db_session)

    assert row.hub_public_url == "http://10.0.0.2:8011"


async def test_reconcile_adds_port_when_url_has_none(db_session, monkeypatch, desktop_env):
    """URL 未带端口时校准后应补上实际端口。"""
    row = await _get_or_create_settings_row(db_session, "http://10.0.0.2")
    monkeypatch.setenv("PORT", "8003")

    await _reconcile_hub_public_url_port(db_session)

    assert row.hub_public_url == "http://10.0.0.2:8003"


async def test_reconcile_noop_when_port_matches(db_session, monkeypatch, desktop_env):
    """端口一致时不改写 URL。"""
    row = await _get_or_create_settings_row(db_session, "http://10.0.0.2:8000")
    monkeypatch.setenv("PORT", "8000")

    await _reconcile_hub_public_url_port(db_session)

    assert row.hub_public_url == "http://10.0.0.2:8000"


async def test_reconcile_noop_without_port_env(db_session, monkeypatch, desktop_env):
    """未设置 PORT 环境变量（源码模式手动启动）时不做校准。"""
    row = await _get_or_create_settings_row(db_session, "http://10.0.0.2:8000")
    monkeypatch.delenv("PORT", raising=False)

    await _reconcile_hub_public_url_port(db_session)

    assert row.hub_public_url == "http://10.0.0.2:8000"


async def test_reconcile_skipped_when_not_desktop(db_session, monkeypatch):
    """非 desktop 环境（源码模式）即使设了 PORT 也不改写持久化配置。

    回归用例：2026-09-12 临时验证实例（PORT=8021）曾把开发库
    hub_public_url 静默改写为 :8021。
    """
    row = await _get_or_create_settings_row(db_session, "http://10.0.0.2:8000")
    monkeypatch.setenv("PORT", "8021")

    await _reconcile_hub_public_url_port(db_session)

    assert row.hub_public_url == "http://10.0.0.2:8000"


async def test_reconcile_noop_when_no_settings_row(db_session, monkeypatch, desktop_env):
    """无设置记录时静默返回，不创建记录、不报错。"""
    await db_session.execute(delete(SystemSettings))
    await db_session.flush()
    monkeypatch.setenv("PORT", "8011")

    await _reconcile_hub_public_url_port(db_session)

    result = await db_session.execute(select(SystemSettings).limit(1))
    assert result.scalar_one_or_none() is None


@pytest.mark.parametrize(
    ("raw_url", "expected"),
    [
        ("http://10.0.0.2:8000/path?tab=1", "http://10.0.0.2:8004/path?tab=1"),
        ("https://hub.example.com", "https://hub.example.com:8004"),
    ],
)
async def test_reconcile_preserves_scheme_path_query(
    db_session, monkeypatch, desktop_env, raw_url, expected
):
    """校准只替换端口部分，scheme/path/query 保持不变。"""
    row = await _get_or_create_settings_row(db_session, raw_url)
    monkeypatch.setenv("PORT", "8004")

    await _reconcile_hub_public_url_port(db_session)

    assert row.hub_public_url == expected
