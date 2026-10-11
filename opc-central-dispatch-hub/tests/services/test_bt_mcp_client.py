"""宝塔 MCP 轻量客户端测试（ADR-049）。"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.bt_mcp_client import (
    BtMcpClient,
    BtMcpError,
    sanitize_arguments,
)

BASE_URL = "https://192.168.1.13:8765/bt-mcp-test/mcp"

_INIT_RESULT = {
    "protocolVersion": "2025-03-26",
    "serverInfo": {"name": "FastMCP", "version": "1.28.1"},
}


def _sse(payload: dict) -> httpx.Response:
    """构造 SSE 形态响应。"""
    return httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        text=f"event: message\ndata: {json.dumps(payload)}\n\n",
    )


def _json_response(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def _rpc_result(result: dict, request_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def make_client(handler) -> BtMcpClient:
    """构建注入 MockTransport 的客户端。"""
    transport = httpx.MockTransport(handler)
    return BtMcpClient(
        url=BASE_URL,
        api_token="btmcp_test_token",
        async_client=httpx.AsyncClient(transport=transport),
    )


async def test_initialize_success_parses_server_info() -> None:
    """UT-C1：握手成功解析 serverInfo（SSE 响应）。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = json.loads(request.content)
        if body["method"] == "initialize":
            assert body["params"]["clientInfo"]["name"] == "tilapia-hub"
            assert request.headers["Authorization"] == "Bearer btmcp_test_token"
            return _sse(_rpc_result(_INIT_RESULT))
        return _json_response({})  # notifications/initialized → 202 由 httpx 视为 2xx

    client = make_client(handler)
    try:
        result = await client.initialize()
        assert result["serverInfo"]["name"] == "FastMCP"
        assert len(calls) == 2  # initialize + initialized 通知
    finally:
        await client.close()


async def test_session_id_carried_on_followup_requests() -> None:
    """UT-C2：有状态服务端返回 Mcp-Session-Id 时后续请求携带。"""
    seen_headers: list[str | None] = []

    def handler_with_session(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("Mcp-Session-Id"))
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "sess-123", "Content-Type": "text/event-stream"},
                text=f"event: message\ndata: {json.dumps(_rpc_result(_INIT_RESULT))}\n\n",
            )
        return _json_response(_rpc_result({"tools": []}))

    transport = httpx.MockTransport(handler_with_session)
    client = BtMcpClient(
        url=BASE_URL,
        api_token="t",
        async_client=httpx.AsyncClient(transport=transport),
    )
    try:
        await client.initialize()
        assert client._session_id == "sess-123"
        await client.list_tools()
        assert seen_headers[-1] == "sess-123"
    finally:
        await client.close()


async def test_list_tools_json_response() -> None:
    """UT-C3：tools/list 纯 JSON 响应解析。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return _sse(_rpc_result(_INIT_RESULT))
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        assert body["method"] == "tools/list"
        tools = [
            {
                "name": "SystemInfo",
                "description": "获取系统信息",
                "inputSchema": {"type": "object"},
            },
            {"name": "SiteList", "description": "获取网站列表", "inputSchema": {"type": "object"}},
        ]
        return _json_response(_rpc_result({"tools": tools}))

    client = make_client(handler)
    try:
        await client.initialize()
        tools = await client.list_tools()
        assert [t["name"] for t in tools] == ["SystemInfo", "SiteList"]
    finally:
        await client.close()


async def test_call_tool_parses_bt_struct() -> None:
    """UT-C4：tools/call 解析宝塔统一结构 {status, msg, data}。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return _sse(_rpc_result(_INIT_RESULT))
        if body["method"] == "tools/call":
            assert body["params"]["name"] == "SystemInfo"
            inner = {"status": True, "msg": "ok", "data": {"cpu": 8}}
            return _json_response(
                _rpc_result({"content": [{"type": "text", "text": json.dumps(inner)}]})
            )
        return _json_response(_rpc_result({}))

    client = make_client(handler)
    try:
        await client.initialize()
        result = await client.call_tool("SystemInfo", {})
        assert result["status"] is True
        assert result["data"]["cpu"] == 8
    finally:
        await client.close()


async def test_call_tool_is_error_wrapped() -> None:
    """工具 isError 时包装为 status=false。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return _sse(_rpc_result(_INIT_RESULT))
        return _json_response(
            _rpc_result({"isError": True, "content": [{"type": "text", "text": "参数缺失"}]})
        )

    client = make_client(handler)
    try:
        await client.initialize()
        result = await client.call_tool("Bad", {})
        assert result["status"] is False
        assert "参数缺失" in result["msg"]
    finally:
        await client.close()


async def test_ip_denied_translated() -> None:
    """UT-C5：403 ip denied → 白名单排查指引。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"status": False, "msg": "ip denied"})

    client = make_client(handler)
    try:
        with pytest.raises(BtMcpError) as exc_info:
            await client.initialize()
        assert exc_info.value.reason == "whitelist"
        assert "白名单" in str(exc_info.value)
    finally:
        await client.close()


async def test_unauthorized_translated() -> None:
    """UT-C6：401 → 令牌失效指引。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    client = make_client(handler)
    try:
        with pytest.raises(BtMcpError) as exc_info:
            await client.initialize()
        assert exc_info.value.reason == "auth"
        assert "令牌" in str(exc_info.value)
    finally:
        await client.close()


async def test_tls_error_translated() -> None:
    """UT-C7：TLS 证书错误 → 证书链指引。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate"
        )

    client = make_client(handler)
    try:
        with pytest.raises(BtMcpError) as exc_info:
            await client.initialize()
        assert exc_info.value.reason == "tls"
        assert "证书" in str(exc_info.value)
    finally:
        await client.close()


async def test_connect_refused_translated() -> None:
    """UT-C8：连接拒绝 → 端口/防火墙指引。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    client = make_client(handler)
    try:
        with pytest.raises(BtMcpError) as exc_info:
            await client.initialize()
        assert exc_info.value.reason == "connect"
        assert "8765" in str(exc_info.value)
    finally:
        await client.close()


def test_ca_cert_loaded_into_ssl_context(monkeypatch) -> None:
    """UT-C9：ca_cert 以 cadata 方式加载进信任上下文（不写临时文件）。"""
    import contextlib
    import ssl

    loaded: list[str] = []
    original = ssl.SSLContext.load_verify_locations

    def spy(self, **kwargs):
        if "cadata" in kwargs:
            loaded.append(kwargs["cadata"])
        return original(self, **kwargs)

    monkeypatch.setattr(ssl.SSLContext, "load_verify_locations", spy)
    pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
    with contextlib.suppress(Exception):
        BtMcpClient(url=BASE_URL, api_token="t", ca_cert=pem)
    assert loaded and loaded[-1] == pem


async def test_timeout_translated() -> None:
    """UT-C10：超时 → 可读错误。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client = make_client(handler)
    try:
        with pytest.raises(BtMcpError) as exc_info:
            await client.initialize()
        assert exc_info.value.reason == "timeout"
    finally:
        await client.close()


def test_sanitize_arguments_masks_sensitive_values() -> None:
    """日志脱敏：密码/令牌/私钥类字段值替换为 ***。"""
    cleaned = sanitize_arguments(
        {
            "name": "db1",
            "password": "p@ss",
            "db_user": "u",
            "nested": {"api_key": "k", "plain": "v"},
        }
    )
    assert cleaned["name"] == "db1"
    assert cleaned["password"] == "***"
    assert cleaned["db_user"] == "u"
    assert cleaned["nested"]["api_key"] == "***"
    assert cleaned["nested"]["plain"] == "v"
