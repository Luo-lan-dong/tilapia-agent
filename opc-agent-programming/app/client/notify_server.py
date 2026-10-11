"""Agent 通知 API 服务（ADR-035 第二步）。

轻量 HTTP 服务（标准库 ``ThreadingHTTPServer``，daemon 线程），仅两个端点：

- ``POST /internal/hub-notify``：中枢重启后反连通知新地址
  （``Authorization: Bearer <api_token>`` 认证，失败 401）；
- ``GET  /internal/health``：健康检查（中枢反连前探活，无认证）。

notify 处理为异步回调：HTTP 线程经 ``run_coroutine_threadsafe`` 把地址更新
与立即重连调度回主事件循环（由 ``HubClient.apply_hub_notify`` 完成），
本模块不持有任何连接状态。未配置 ``API_TOKEN`` 时不启动服务（避免暴露
无认证端点）。
"""

import asyncio
import hmac
import json
import logging
import threading
from collections.abc import Awaitable, Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)

_NOTIFY_PATH = "/internal/hub-notify"
_HEALTH_PATH = "/internal/health"
# 请求体上限：notify payload 仅两个 URL 字段，超限直接拒绝
_MAX_BODY_BYTES = 64 * 1024


class NotifyServer:
    """Agent 通知 API 服务（仅内网使用，api_token 认证）。"""

    def __init__(
        self,
        api_token: str,
        host: str,
        port: int,
        on_notify: Callable[[str, str | None], Awaitable[None]],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """初始化通知服务。

        Args:
            api_token: 通知 API 认证 Token（安装脚本生成，与注册上报一致）。
            host: 监听地址（内网地址，避免公网暴露）。
            port: 监听端口（默认 18080）。
            on_notify: notify 处理回调（在主事件循环执行）。
            loop: 主事件循环（回调调度目标）。
        """
        self._api_token = api_token
        self._host = host
        self._port = port
        self._on_notify = on_notify
        self._loop = loop
        self._http_server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        """通知 API 基础地址（日志用）。"""
        return f"http://{self._host}:{self._port}"

    @property
    def bound_port(self) -> int:
        """实际绑定端口（host 配置 0 时由系统分配，测试用）。"""
        if self._http_server is None:
            raise RuntimeError("通知服务未启动")
        return int(self._http_server.server_address[1])

    def start(self) -> None:
        """启动 HTTP 服务 daemon 线程（端口占用等异常直接抛给调用方）。"""
        server = ThreadingHTTPServer((self._host, self._port), self._make_handler())
        server.daemon_threads = True
        self._http_server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="agent-notify-server",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "notify_server_started",
            extra={"event": "notify_server_started", "listen": self.base_url},
        )

    def stop(self) -> None:
        """停止 HTTP 服务线程（幂等）。"""
        if self._http_server is not None:
            self._http_server.shutdown()
            self._http_server.server_close()
            self._http_server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("notify_server_stopped", extra={"event": "notify_server_stopped"})

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        """构造绑定本服务实例的请求处理器类。"""
        notify_server = self

        class _Handler(BaseHTTPRequestHandler):
            """notify / health 请求处理器（通过 handler.server 取服务实例）。"""

            def _send_json(self, status_code: int, body: dict) -> None:
                """输出 JSON 响应。"""
                data = json.dumps(body).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802 标准库处理器命名
                """健康检查（无认证）。"""
                if self.path != _HEALTH_PATH:
                    self._send_json(404, {"code": 100004, "message": "not found"})
                    return
                self._send_json(200, {"code": 0, "message": "ok"})
                logger.debug("notify_health_checked", extra={"event": "notify_health_checked"})

            def do_POST(self) -> None:  # noqa: N802 标准库处理器命名
                """中枢反连通知（Bearer api_token 认证）。"""
                if self.path != _NOTIFY_PATH:
                    self._send_json(404, {"code": 100004, "message": "not found"})
                    return
                auth = self.headers.get("Authorization", "")
                if not hmac.compare_digest(auth, f"Bearer {notify_server._api_token}"):
                    logger.warning(
                        "notify_auth_failed",
                        extra={"event": "notify_auth_failed", "client_ip": self.client_address[0]},
                    )
                    self._send_json(401, {"code": 100001, "message": "unauthorized"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send_json(400, {"code": 100001, "message": "bad request"})
                    return
                if length <= 0 or length > _MAX_BODY_BYTES:
                    self._send_json(400, {"code": 100001, "message": "bad request"})
                    return
                try:
                    payload = json.loads(self.rfile.read(length))
                    hub_public_url = payload["hub_public_url"]
                    if not isinstance(hub_public_url, str) or not hub_public_url:
                        raise ValueError("hub_public_url required")
                    hub_ws_url = payload.get("hub_ws_url")
                    if hub_ws_url is not None and not isinstance(hub_ws_url, str):
                        raise ValueError("hub_ws_url must be string")
                except (json.JSONDecodeError, KeyError, ValueError):
                    self._send_json(400, {"code": 100001, "message": "bad request"})
                    return

                # 调度回主事件循环执行地址更新 + 立即重连（同步返回受理结果）
                asyncio.run_coroutine_threadsafe(
                    notify_server._on_notify(hub_public_url, hub_ws_url),
                    notify_server._loop,
                )
                logger.info(
                    "notify_received",
                    extra={
                        "event": "notify_received",
                        "client_ip": self.client_address[0],
                        "hub_public_url": hub_public_url,
                    },
                )
                self._send_json(200, {"code": 0, "message": "ok"})

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                """屏蔽标准库默认 access log（由结构化日志事件替代）。"""

        return _Handler
