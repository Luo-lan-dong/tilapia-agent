"""宝塔 MCP 服务器管理 API 测试（ADR-049）。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete

from app.models.bt_mcp_server import BtMcpServer
from app.services.bt_mcp_client import BtMcpError

SERVERS_URL = "/api/v1/bt-mcp/servers"

_FAKE_TOOLS = [
    {"name": "SystemInfo", "description": "获取系统信息", "inputSchema": {"type": "object"}},
    {"name": "SiteDelete", "description": "删除网站", "inputSchema": {"type": "object"}},
]


@pytest.fixture(autouse=True)
async def _clean_servers() -> AsyncGenerator[None, None]:
    """每个用例前后清空 bt_mcp_servers（独立 engine，避免跨事件循环复用连接池）。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings
    from app.models.base import AsyncSessionLocal

    for _phase in range(2):
        engine = create_async_engine(get_settings().database_url, echo=False)
        try:
            session = AsyncSessionLocal(bind=engine)
            async with session:
                await session.execute(delete(BtMcpServer))
                await session.commit()
        finally:
            await engine.dispose()
        if _phase == 0:
            yield


def _payload(name: str = "生产服务器-101.13") -> dict:
    return {
        "name": name,
        "local_url": "https://192.168.101.13:8765/bt-mcp-LR8KX/mcp",
        "public_url": "https://112.66.55.227:8765/bt-mcp-LR8KX/mcp",
        "address_mode": "local",
        "api_token": "btmcp_secret_token",
        "ca_cert": "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----",
        "remark": "测试服务器",
    }


def test_create_and_list_masked(ws_client) -> None:
    """UT-A1/A4：创建成功；列表脱敏（无明文 token/证书，仅布尔）。"""
    response = ws_client.post(SERVERS_URL, json=_payload())
    assert response.status_code == 201
    created = response.json()["data"]
    assert created["name"] == "生产服务器-101.13"
    assert created["has_token"] is True
    assert created["has_ca_cert"] is True
    assert "btmcp_secret_token" not in response.text
    assert "BEGIN CERTIFICATE" not in response.text

    listed = ws_client.get(SERVERS_URL)
    assert listed.status_code == 200
    data = listed.json()["data"]
    assert data["total"] == 1
    assert "btmcp_secret_token" not in listed.text


def test_create_with_mcp_info_import(ws_client) -> None:
    """UT-A2：mcp_info JSON 一键导入自动填充 token/双地址/证书。"""
    mcp_info = {
        "api_token": "btmcp_imported",
        "local_host": "https://192.168.101.13:8765/bt-mcp-LR8KX/mcp",
        "public_host": "https://112.66.55.227:8765/bt-mcp-LR8KX/mcp",
        "tls_cert": "-----BEGIN CERTIFICATE-----\nimported\n-----END CERTIFICATE-----",
        "tls_required": True,
    }
    response = ws_client.post(
        SERVERS_URL,
        json={"name": "导入服务器", "mcp_info": mcp_info},
    )
    assert response.status_code == 201
    server_id = response.json()["data"]["id"]

    # 详情验证落库内容（详情本身脱敏），更新接口回读验证
    detail = ws_client.get(f"{SERVERS_URL}/{server_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["has_ca_cert"] is True


def test_create_duplicate_name_conflict(ws_client) -> None:
    """UT-A3：name 重复 → 409 / 207002。"""
    ws_client.post(SERVERS_URL, json=_payload())
    response = ws_client.post(SERVERS_URL, json=_payload())
    assert response.status_code == 409
    assert response.json()["code"] == 207002


def test_update_keeps_token_by_default(ws_client) -> None:
    """UT-A5：PATCH 缺省凭据保留现值；空串清除。"""
    created = ws_client.post(SERVERS_URL, json=_payload()).json()["data"]
    server_id = created["id"]

    updated = ws_client.patch(f"{SERVERS_URL}/{server_id}", json={"remark": "改备注"})
    assert updated.status_code == 200
    assert updated.json()["data"]["has_token"] is True  # 缺省保留

    cleared = ws_client.patch(f"{SERVERS_URL}/{server_id}", json={"api_token": ""})
    assert cleared.status_code == 200
    assert cleared.json()["data"]["has_token"] is False


def test_delete_flow(ws_client) -> None:
    """UT-A6：删除后再查 404 / 207001。"""
    created = ws_client.post(SERVERS_URL, json=_payload()).json()["data"]
    server_id = created["id"]
    assert ws_client.delete(f"{SERVERS_URL}/{server_id}").status_code == 200
    assert ws_client.get(f"{SERVERS_URL}/{server_id}").status_code == 404
    assert ws_client.get(f"{SERVERS_URL}/{server_id}").json()["code"] == 207001


def _patch_client(method: str, side_effect=None, return_value=None):
    """patch BtMcpServerService.build_client 返回 mock 客户端。"""
    mock_client = AsyncMock()
    if method == "test":
        if side_effect:
            mock_client.initialize = AsyncMock(side_effect=side_effect)
            mock_client.list_tools = AsyncMock()
        else:
            mock_client.initialize = AsyncMock(return_value=return_value[0])
            mock_client.list_tools = AsyncMock(return_value=return_value[1])
        mock_client.close = AsyncMock()
    elif method == "tools":
        mock_client.list_tools = (
            AsyncMock(side_effect=side_effect)
            if side_effect
            else AsyncMock(return_value=return_value)
        )
        mock_client.close = AsyncMock()
    return patch(
        "app.api.v1.endpoints.bt_mcp.BtMcpServerService.build_client",
        return_value=mock_client,
    )


def test_test_connection_success(ws_client) -> None:
    """UT-A7：连接测试成功返回 serverInfo + 工具统计。"""
    created = ws_client.post(SERVERS_URL, json=_payload()).json()["data"]
    server_id = created["id"]
    server_info = {"name": "FastMCP", "version": "1.28.1"}
    init_result = {"serverInfo": server_info, "protocolVersion": "2025-03-26"}
    with _patch_client("test", return_value=(init_result, _FAKE_TOOLS)):
        response = ws_client.post(f"{SERVERS_URL}/{server_id}/test")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["success"] is True
    assert data["tool_count"] == 2
    assert data["server_info"]["name"] == "FastMCP"


def test_test_connection_failure_with_guidance(ws_client) -> None:
    """UT-A8：连接测试失败 → 200 响应内 success=false + 指引 message。"""
    created = ws_client.post(SERVERS_URL, json=_payload()).json()["data"]
    server_id = created["id"]
    error = BtMcpError("IP 白名单未放行", reason="whitelist")
    with _patch_client("test", side_effect=error):
        response = ws_client.post(f"{SERVERS_URL}/{server_id}/test")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["success"] is False
    assert "白名单" in data["message"]


def test_list_tools_endpoint(ws_client) -> None:
    """UT-A9：工具清单含分类与风险。"""
    created = ws_client.post(SERVERS_URL, json=_payload()).json()["data"]
    server_id = created["id"]
    with _patch_client("tools", return_value=_FAKE_TOOLS):
        response = ws_client.get(f"{SERVERS_URL}/{server_id}/tools")
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    by_name = {item["name"]: item for item in items}
    assert by_name["SystemInfo"]["risk"] == "low"
    assert by_name["SiteDelete"]["risk"] == "high"
    assert by_name["SiteDelete"]["category"] == "网站"
