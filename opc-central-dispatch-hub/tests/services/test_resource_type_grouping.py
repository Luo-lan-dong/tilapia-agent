"""ADR-025 资源类型分组与地址下发单元测试。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.resource_sync_service import (
    ResourceSyncService,
    parse_resource_key,
)


class TestParseResourceKey:
    """key v2 规范解析。"""

    def test_package_key(self) -> None:
        assert parse_resource_key("agent-code-linux-x64") == ("package", "code")

    def test_script_key(self) -> None:
        assert parse_resource_key("install-script-code") == ("script", "code")

    def test_legacy_script_key(self) -> None:
        # 历史 key install-script-linux 解析出 linux 类型（未分组语义由调用方处理）
        assert parse_resource_key("install-script-linux") == ("script", "linux")

    def test_unknown_key(self) -> None:
        assert parse_resource_key("random-key") is None

    def test_empty(self) -> None:
        assert parse_resource_key("") is None


@pytest.fixture
def service(tmp_path: Path) -> ResourceSyncService:
    return ResourceSyncService(cache_dir=tmp_path)


def _entry(key: str, sha: str, version: str = "") -> dict:
    return {"key": key, "path": f"p/{key}", "sha256": sha, "size": 10, "version": version}


class TestTypeGrouping:
    """状态视图按类型分组。"""

    def test_grouped_view(self, service: ResourceSyncService) -> None:
        """code 类型的包+脚本聚合到同一组；has_update 聚合。"""
        service._save_local_manifest(
            {
                "revision": "r1",
                "resources": [
                    _entry("agent-code-linux-x64", "a" * 64, "0.5.0"),
                    _entry("install-script-code", "b" * 64),
                ],
            }
        )
        service._update_state(
            status="update-available",
            cloud_snapshot={
                "revision": "r2",
                "resources": [
                    _entry("agent-code-linux-x64", "a" * 64, "0.5.0"),
                    _entry("install-script-code", "c" * 64),
                ],
            },
        )
        view = service.get_status_view()
        assert len(view["agent_types"]) == 1
        group = view["agent_types"][0]
        assert group["type"] == "code"
        # 脚本 sha 变化 → 类型级 has_update
        assert group["has_update"] is True
        assert set(group["resources"]) == {"agent-code-linux-x64", "install-script-code"}

    def test_ungrouped_entries(self, service: ResourceSyncService) -> None:
        """无法解析的 key 归入 _ungrouped。"""
        service._save_local_manifest(
            {"revision": "r1", "resources": [_entry("legacy-key", "a" * 64)]}
        )
        service._update_state(
            status="up-to-date",
            cloud_snapshot={"revision": "r1", "resources": [_entry("legacy-key", "a" * 64)]},
        )
        view = service.get_status_view()
        assert view["agent_types"][0]["type"] == "_ungrouped"

    def test_keys_for_type(self, service: ResourceSyncService) -> None:
        """keys_for_type 返回该类型全部 key（云端快照为准）。"""
        service._update_state(
            cloud_snapshot={
                "revision": "r2",
                "resources": [
                    _entry("agent-code-linux-x64", "a" * 64, "0.5.0"),
                    _entry("install-script-code", "b" * 64),
                ],
            }
        )
        assert service.keys_for_type("code") == [
            "agent-code-linux-x64",
            "install-script-code",
        ]
        assert service.keys_for_type("video") == []


def _mock_settings(config_url: str = "", token: str = "") -> MagicMock:
    """构造带资源 hub 配置的 get_settings 替身。"""
    cfg = MagicMock()
    cfg.agent_resource_hub_config_url = config_url
    cfg.agent_resource_hub_token = token
    return cfg


class TestResolveManifestUrl:
    """地址云端下发（服务端拉取 + 缓存回退，配置文件驱动）。"""

    async def test_fetch_success_updates_cache(self, service: ResourceSyncService) -> None:
        """拉取成功 → 返回新地址并写 state 缓存。"""
        resp = AsyncMock()
        resp.status_code = 200
        resp.raise_for_status = lambda: None
        resp.json = lambda: {"data": {"manifestUrl": "https://new.example.com/agent/"}}
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        def _factory(**_kwargs):
            return client

        with (
            patch(
                "app.services.resource_sync_service.get_settings",
                return_value=_mock_settings("http://server/portal/open/hub/config", "tok"),
            ),
            patch("app.services.resource_sync_service.httpx.AsyncClient", _factory),
        ):
            url = await service.resolve_manifest_url()
        assert url == "https://new.example.com/agent/"
        assert service._read_state()["manifest_url"] == "https://new.example.com/agent/"

    async def test_fetch_failure_falls_back_cache(self, service: ResourceSyncService) -> None:
        """服务端不可达 → 回退 state 缓存的最近地址。"""
        service._update_state(manifest_url="https://cached.example.com/agent/")

        def _factory(**_kwargs):
            raise ConnectionError("server down")

        with (
            patch(
                "app.services.resource_sync_service.get_settings",
                return_value=_mock_settings("http://server/portal/open/hub/config", "tok"),
            ),
            patch("app.services.resource_sync_service.httpx.AsyncClient", _factory),
        ):
            url = await service.resolve_manifest_url()
        assert url == "https://cached.example.com/agent/"

    async def test_no_config_returns_none(self, service: ResourceSyncService) -> None:
        """服务端地址未配置 → 返回 None（调用方回退本地配置）。"""
        with patch(
            "app.services.resource_sync_service.get_settings",
            return_value=_mock_settings("", ""),
        ):
            assert await service.resolve_manifest_url() is None

    async def test_failure_without_cache_returns_none(self, service: ResourceSyncService) -> None:
        """服务端不可达且无缓存 → None。"""

        def _factory(**_kwargs):
            raise ConnectionError("server down")

        with (
            patch(
                "app.services.resource_sync_service.get_settings",
                return_value=_mock_settings("http://server/portal/open/hub/config", "tok"),
            ),
            patch("app.services.resource_sync_service.httpx.AsyncClient", _factory),
        ):
            assert await service.resolve_manifest_url() is None
