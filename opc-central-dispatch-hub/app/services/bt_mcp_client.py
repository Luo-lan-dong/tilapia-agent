"""宝塔 MCP 轻量客户端（ADR-049）。

自研实现（否决官方 SDK）：宝塔 MCP 服务端为 FastMCP 无状态模式，
协议面仅 3 个方法（initialize / tools/list / tools/call），httpx 异步
直调即可，换取零依赖与完全可控的错误翻译。

- TLS：``ca_cert``（PEM 证书链全文）以 ``load_verify_locations(cadata)``
  内存加载构建信任上下文，**不跳过校验**；未配置时走系统信任链。
- 会话：无状态服务端不返回 ``Mcp-Session-Id``；若返回则缓存并在后续
  请求携带（兼容未来有状态模式）。
- 错误翻译：把 HTTP/网络层错误翻译为面向用户的中文排查指引
  （白名单/令牌/证书/端口），供 API 响应与编排对话直接展示。
"""

import json
import logging
import ssl
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60.0
_CLIENT_INFO = {"name": "tilapia-hub", "version": "0.1.0"}
_PROTOCOL_VERSION = "2025-03-26"

#: 响应体错误摘要截断上限（防超长 HTML 错误页刷屏）
_ERROR_BODY_MAX = 300


class BtMcpError(Exception):
    """宝塔 MCP 调用失败（message 面向用户，含排查指引）。"""

    def __init__(self, message: str, reason: str = "unknown") -> None:
        super().__init__(message)
        #: 失败原因分类：whitelist / auth / tls / connect / timeout / http / protocol / tool
        self.reason = reason


def _body_snippet(body: str) -> str:
    """截取响应体摘要用于错误信息。"""
    return body.strip().replace("\n", " ")[:_ERROR_BODY_MAX]


def _translate_http_error(status_code: int, body: str) -> BtMcpError:
    """把 HTTP 错误响应翻译为带排查指引的 BtMcpError。"""
    lowered = body.lower()
    if status_code == 403 and "ip denied" in lowered:
        return BtMcpError(
            "连接被拒绝：目标服务器宝塔 MCP 插件的 IP 白名单未放行中枢出口 IP。"
            "请在宝塔面板【软件商店 → 宝塔 MCP 服务 → 接入与体验】把中枢所在"
            "机器的出口 IP 加入白名单后重试（若中枢与服务器同网段，加内网 IP；"
            "公网访问则加公网出口 IP）。",
            reason="whitelist",
        )
    if status_code in (401, 403):
        return BtMcpError(
            "鉴权失败：授权令牌无效或已被重新生成。请在宝塔面板插件的"
            "【安全授权】页面重新获取令牌并更新该服务器配置。",
            reason="auth",
        )
    return BtMcpError(
        f"宝塔 MCP 服务返回 HTTP {status_code}：{_body_snippet(body)}",
        reason="http",
    )


def _translate_network_error(exc: Exception) -> BtMcpError:
    """把网络层异常翻译为带排查指引的 BtMcpError。"""
    if isinstance(exc, httpx.ConnectError):
        text = str(exc)
        if "certificate" in text.lower() or "ssl" in text.lower():
            return BtMcpError(
                "TLS 证书校验失败：服务器使用自签证书但未配置证书链，或证书链已更换。"
                "请在宝塔面板插件的【接入与体验】页面重新获取接入信息，"
                "更新该服务器配置中的 CA 证书。",
                reason="tls",
            )
        return BtMcpError(
            f"无法连接宝塔 MCP 服务：{text}。请检查面板防火墙与云服务器安全组"
            "是否已放行 TCP 8765 端口、服务器地址是否正确。",
            reason="connect",
        )
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return BtMcpError(
            "连接宝塔 MCP 服务超时：网络不可达或服务未响应，请检查端口放行与服务器负载。",
            reason="timeout",
        )
    if isinstance(exc, ssl.SSLError):
        return BtMcpError(
            "TLS 证书校验失败：请重新获取接入信息并更新 CA 证书链配置。",
            reason="tls",
        )
    return BtMcpError(f"宝塔 MCP 网络请求失败：{exc}", reason="connect")


def _parse_response(raw: str) -> dict[str, Any]:
    """解析 MCP 响应：兼容 SSE（data: 行）与纯 JSON 两种形态。"""
    for line in raw.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return json.loads(raw)


class BtMcpClient:
    """宝塔 MCP 异步客户端（每次请求级使用，用完关闭）。"""

    def __init__(
        self,
        url: str,
        api_token: str,
        ca_cert: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        async_client: httpx.AsyncClient | None = None,
    ) -> None:
        """初始化客户端。

        Args:
            url: MCP 服务地址（local_url 或 public_url，按 address_mode 选定）。
            api_token: Bearer 授权令牌。
            ca_cert: 自签 CA 证书链 PEM 全文（None 时走系统信任链）。
            timeout_seconds: 请求超时（部署类工具可能较慢，默认 60s）。
            async_client: 外部注入的 httpx 客户端（测试用）。
        """
        self._url = url
        self._api_token = api_token
        self._session_id: str | None = None
        default_headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json, text/event-stream",
        }
        self._owns_client = async_client is None
        if async_client is not None:
            # 外部注入时合并协议必需头（认证与 SSE 协商），保留调用方既有头
            for key, value in default_headers.items():
                async_client.headers.setdefault(key, value)
            self._client = async_client
        else:
            verify: ssl.SSLContext | bool = True
            if ca_cert:
                verify = ssl.create_default_context()
                verify.load_verify_locations(cadata=ca_cert)
            self._client = httpx.AsyncClient(
                headers=default_headers,
                timeout=timeout_seconds,
                verify=verify,
            )

    async def close(self) -> None:
        """关闭底层 HTTP 客户端（外部注入时不关闭）。"""
        if self._owns_client:
            await self._client.aclose()

    async def _rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        notify: bool = False,
    ) -> dict[str, Any]:
        """发送一条 JSON-RPC 请求并解析结果（通知无响应体）。"""
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            body["id"] = 1
        headers = {"Content-Type": "application/json"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            response = await self._client.post(self._url, content=json.dumps(body), headers=headers)
        except (httpx.HTTPError, ssl.SSLError) as exc:
            raise _translate_network_error(exc) from exc
        if response.status_code >= 400:
            raise _translate_http_error(response.status_code, response.text)
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id
        if notify:
            return {}
        try:
            payload = _parse_response(response.text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise BtMcpError(
                f"宝塔 MCP 响应解析失败：{_body_snippet(response.text)}",
                reason="protocol",
            ) from exc
        if payload.get("error"):
            err = payload["error"]
            raise BtMcpError(
                f"宝塔 MCP 协议错误：{err.get('message', '未知错误')}",
                reason="protocol",
            )
        return payload.get("result", {})

    async def initialize(self) -> dict[str, Any]:
        """握手并返回 serverInfo/protocolVersion/capabilities。"""
        result = await self._rpc(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            },
        )
        await self._rpc("notifications/initialized", {}, notify=True)
        return result

    async def list_tools(self) -> list[dict[str, Any]]:
        """拉取工具清单（name/description/inputSchema/annotations）。"""
        result = await self._rpc("tools/list", {})
        tools = result.get("tools", [])
        # 无状态服务端分页时循环取全（Next-Cursor），有则继续
        cursor = result.get("nextCursor")
        while cursor:
            result = await self._rpc("tools/list", {"cursor": cursor})
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """调用一个 MCP 工具，返回宝塔统一结构 ``{status, msg, ...}``。

        服务端协议错误或 HTTP 层失败抛 :class:`BtMcpError`；工具业务层
        失败（``status=false``）不抛异常，由调用方按 msg 处置。
        """
        result = await self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        if result.get("isError"):
            content = result.get("content", [])
            text = content[0].get("text", "") if content else ""
            return {"status": False, "msg": text or "工具执行失败（无返回信息）"}
        return _extract_tool_result(result)


def _extract_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    """把 tools/call 的 content 内容提取为宝塔统一结构。

    宝塔工具正常返回 JSON 文本 ``{status, msg, ...}``；文本形式时包装为
    ``{"status": True, "msg": "", "data": {"text": ...}}``。
    """
    content = result.get("content", [])
    if not content:
        return {"status": True, "msg": "", "data": None}
    first = content[0]
    if first.get("type") == "text":
        text = first.get("text", "")
        try:
            parsed = json.loads(text)
        except (ValueError, json.JSONDecodeError):
            return {"status": True, "msg": "", "data": {"text": text}}
        if isinstance(parsed, dict):
            return parsed
        return {"status": True, "msg": "", "data": parsed}
    return {"status": True, "msg": "", "data": {"content": content}}


def sanitize_arguments(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """脱敏工具入参（密码/令牌/密钥类字段值替换，供日志与审计使用）。"""
    sensitive_keys = {"password", "token", "secret", "api_key", "private_key", "passwd"}
    if not arguments:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in arguments.items():
        if any(word in key.lower() for word in sensitive_keys):
            cleaned[key] = "***"
        elif isinstance(value, dict):
            cleaned[key] = sanitize_arguments(value)
        else:
            cleaned[key] = value
    return cleaned


async def timed_call(
    client: BtMcpClient,
    tool: str,
    arguments: dict[str, Any] | None,
) -> tuple[dict[str, Any], int]:
    """带耗时的工具调用（技能与测试端点共用），返回 (结果, 耗时毫秒)。"""
    start = time.monotonic()
    result = await client.call_tool(tool, arguments)
    return result, int((time.monotonic() - start) * 1000)
