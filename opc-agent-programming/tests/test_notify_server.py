"""Agent 通知 API 服务单元测试（ADR-035）：真实 ThreadingHTTPServer + 随机端口。

覆盖 Harness UT-3（正确 Bearer token → 地址更新并立即重连）、UT-4（错误/缺失
token → 401 且地址不变）、UT-6（仅监听配置地址，AGENT_API_PORT 生效）。
"""

import asyncio
import socket

import httpx
import pytest

from app.client.notify_server import NotifyServer


def _free_port() -> int:
    """获取随机可用端口。"""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def notified() -> list[tuple[str, str | None]]:
    """记录 notify 回调收到的 (hub_public_url, hub_ws_url)。"""
    return []


@pytest.fixture
def notify_event() -> asyncio.Event:
    """回调触发的异步事件（供测试等待）。"""
    return asyncio.Event()


@pytest.fixture
async def server(
    notified: list[tuple[str, str | None]],
    notify_event: asyncio.Event,
) -> NotifyServer:
    """启动随机端口的通知服务，测试结束停止。"""

    async def on_notify(hub_public_url: str, hub_ws_url: str | None) -> None:
        """记录回调参数并置位事件。"""
        notified.append((hub_public_url, hub_ws_url))
        notify_event.set()

    srv = NotifyServer(
        api_token="secret-token",
        host="127.0.0.1",
        port=0,  # 由系统分配实际端口（UT-6 经 bound_port 断言配置生效）
        on_notify=on_notify,
        loop=asyncio.get_running_loop(),
    )
    srv.start()
    yield srv
    srv.stop()


async def test_notify_with_correct_token_invokes_callback(
    server: NotifyServer,
    notified: list[tuple[str, str | None]],
    notify_event: asyncio.Event,
) -> None:
    """UT-3：正确 Bearer token → 200，回调收到 hub_public_url/hub_ws_url。"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"http://127.0.0.1:{server.bound_port}/internal/hub-notify",
            json={"hub_public_url": "http://10.0.0.1:8005", "hub_ws_url": "ws://10.0.0.1:8005/ws"},
            headers={"Authorization": "Bearer secret-token"},
        )

    await asyncio.wait_for(notify_event.wait(), timeout=5)
    assert response.status_code == 200
    assert notified == [("http://10.0.0.1:8005", "ws://10.0.0.1:8005/ws")]


async def test_notify_with_wrong_or_missing_token_rejected(
    server: NotifyServer,
    notified: list[tuple[str, str | None]],
) -> None:
    """UT-4：错误/缺失 token → 401，回调不被触发，地址不变。"""
    base = f"http://127.0.0.1:{server.bound_port}/internal/hub-notify"
    payload = {"hub_public_url": "http://10.0.0.1:8005"}
    async with httpx.AsyncClient() as client:
        wrong = await client.post(
            base, json=payload, headers={"Authorization": "Bearer wrong-token"}
        )
        missing = await client.post(base, json=payload)

    assert wrong.status_code == 401
    assert missing.status_code == 401
    assert notified == []  # 地址未更新


async def test_health_endpoint_no_auth(
    server: NotifyServer,
) -> None:
    """health 端点无认证返回 200（中枢反连前探活）。"""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://127.0.0.1:{server.bound_port}/internal/health")
    assert response.status_code == 200
    assert response.json()["code"] == 0


async def test_invalid_payload_rejected(
    server: NotifyServer,
    notified: list[tuple[str, str | None]],
) -> None:
    """缺少 hub_public_url 的请求 → 400，回调不触发。"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"http://127.0.0.1:{server.bound_port}/internal/hub-notify",
            json={},
            headers={"Authorization": "Bearer secret-token"},
        )
    assert response.status_code == 400
    assert notified == []


async def test_binds_configured_port(
    notified: list[tuple[str, str | None]],
    notify_event: asyncio.Event,
) -> None:
    """UT-6：服务绑定配置指定的地址端口（AGENT_API_PORT 配置生效路径）。"""

    async def on_notify(url: str, ws: str | None) -> None:
        """记录回调。"""
        notified.append((url, ws))
        notify_event.set()

    port = _free_port()
    srv = NotifyServer(
        api_token="t",
        host="127.0.0.1",
        port=port,
        on_notify=on_notify,
        loop=asyncio.get_running_loop(),
    )
    srv.start()
    try:
        assert srv.bound_port == port
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://127.0.0.1:{port}/internal/health")
        assert response.status_code == 200
    finally:
        srv.stop()
