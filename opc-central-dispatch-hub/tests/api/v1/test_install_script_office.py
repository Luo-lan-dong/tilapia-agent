"""ADR-062 office 节点安装脚本契约测试（Harness C-3）。"""

from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.resource_sync_service import ResourceSyncService


@pytest_asyncio.fixture
async def http_client(
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncClient, None]:
    """HTTP 测试客户端（调度器不启动）。"""
    from app.api.deps import get_async_session

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    def _noop(*_args, **_kwargs):  # pragma: no cover - simple noop
        import asyncio

        return asyncio.Event(), None

    with (
        patch("app.main.start_scheduler", _noop),
        patch("app.main.start_heartbeat_monitor", _noop),
    ):
        from app.main import create_app

        app = create_app()
        app.dependency_overrides[get_async_session] = _override
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


def _fake_office_manifest(self: ResourceSyncService) -> dict:  # noqa: ARG001
    return {
        "revision": "test-rev",
        "resources": [
            {
                "key": "install-script-office",
                "path": "install-scripts/office/install-agent-office-linux.sh",
                "sha256": "e" * 64,
                "size": 8000,
                "version": "0.1.0",
            }
        ],
    }


async def test_install_script_supports_office_linux(
    http_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C-3：office/linux 安装脚本矩阵支持（ADR-062）。"""
    monkeypatch.setattr(ResourceSyncService, "local_manifest", _fake_office_manifest)

    response = await http_client.get(
        "/api/v1/agent-nodes/install-scripts",
        params={"agent_type": "office", "os": "linux"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["agent_type"] == "office"
    assert "static/install-scripts/office/install-agent-office-linux.sh" in body["data"]["script_url"]
    assert body["data"]["checksum"] == "sha256:" + "e" * 64


async def test_install_script_office_missing_manifest(
    http_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """脚本未发布/未同步时明确报错（不给错配脚本）。"""

    def _empty_manifest(self: ResourceSyncService) -> dict:  # noqa: ARG001
        return {"revision": "test-rev", "resources": []}

    monkeypatch.setattr(ResourceSyncService, "local_manifest", _empty_manifest)

    response = await http_client.get(
        "/api/v1/agent-nodes/install-scripts",
        params={"agent_type": "office", "os": "linux"},
    )

    assert response.status_code == 404
    body = response.json()
    from app.core.errors import ErrorCode

    assert body["code"] == ErrorCode.INSTALL_SCRIPT_NOT_SUPPORTED
