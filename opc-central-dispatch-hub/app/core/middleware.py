"""HTTP 中间件：request_id 注入。"""

import json
import uuid
from collections.abc import Callable

from fastapi import Request
from starlette.types import Receive, Scope, Send


def _generate_request_id() -> str:
    """生成唯一请求 ID。"""
    return f"req_{uuid.uuid4().hex}"


def _get_request_id(request: Request) -> str:
    """从请求状态中获取 request_id，缺失时生成一个兜底值。"""
    return getattr(request.state, "request_id", _generate_request_id())


def _get_content_type(headers: list[tuple[bytes, bytes]]) -> str:
    """从响应头中解析 content-type（小写）。"""
    for name, value in headers:
        if name.lower() == b"content-type":
            return value.decode("latin-1").lower()
    return ""


def _update_content_length(headers: list[tuple[bytes, bytes]], length: int) -> None:
    """更新响应头中的 content-length。"""
    for index, (name, _) in enumerate(headers):
        if name.lower() == b"content-length":
            headers[index] = (name, str(length).encode())
            return


class RequestIdInjectionMiddleware:
    """ASGI 中间件：生成 request_id 并注入 JSON 响应体。

    BaseHTTPMiddleware 会把响应包装为 ``_StreamingResponse``，导致无法直接读取
    ``JSONResponse.body``。因此使用 ASGI 层中间件拦截 ``send`` 消息，在响应发送
    前完成 request_id 注入。
    """

    def __init__(self, app: Callable[[Scope, Receive, Send], None]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _generate_request_id()
        scope.setdefault("state", {})["request_id"] = request_id

        response_started = False
        start_message: dict = {}
        body_chunks: list[bytes] = []
        sse_start_sent = False

        async def wrapped_send(message: dict) -> None:
            nonlocal response_started, sse_start_sent

            if message["type"] == "http.response.start":
                response_started = True
                start_message.update(message)
                return

            if message["type"] == "http.response.body":
                content_type = _get_content_type(start_message.get("headers", []))
                if "text/event-stream" in content_type:
                    if not sse_start_sent:
                        await send(start_message)
                        sse_start_sent = True
                    await send(message)
                    return

                body_chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return

                full_body = b"".join(body_chunks)
                if response_started and _get_content_type(
                    start_message.get("headers", [])
                ).startswith("application/json"):
                    try:
                        data = json.loads(full_body)
                        if isinstance(data, dict) and "request_id" not in data:
                            data["request_id"] = request_id
                            full_body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                            _update_content_length(start_message.get("headers", []), len(full_body))
                    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                        pass

                await send(start_message)
                await send({"type": "http.response.body", "body": full_body, "more_body": False})
                return

            await send(message)

        await self.app(scope, receive, wrapped_send)
