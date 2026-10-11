"""EndpointProber 并行竞速探测单元测试（ADR-035）：respx 拦截 HTTP，无真实网络。

覆盖 Harness UT-1（首个成功端点胜出并锁定；全部失败按间隔重试整环）与
UT-2（单次探测 1s 超时，慢端点不阻塞快端点胜出）。
"""

import asyncio
import time

import httpx
import pytest
import respx

from app.client.endpoint_probe import PROBE_TIMEOUT_SECONDS, EndpointProber
from app.config import AgentConfig

_HUB_HOST = "10.0.0.2"


@pytest.fixture
def config() -> AgentConfig:
    """测试配置：首选地址指向 10.0.0.2:8000。"""
    cfg = AgentConfig(agent_registration_token="test-token")
    cfg.hub_api_url = f"http://{_HUB_HOST}:8000"
    return cfg


@pytest.fixture
def prober(config: AgentConfig) -> EndpointProber:
    """构造端点探测器（payload 构造器返回最小占位）。"""
    return EndpointProber(config, lambda: {"client_id": "c-1"})


def _candidate_urls(prober: EndpointProber) -> list[str]:
    """展开候选环 URL 列表。"""
    return prober.candidate_api_urls()


@respx.mock
async def test_race_first_success_wins_and_locks(
    prober: EndpointProber,
) -> None:
    """UT-1：并行竞速中首个注册成功的端点胜出并锁定，其余探测被取消。"""

    async def _slow_success(request: httpx.Request) -> httpx.Response:
        """慢速成功端点（延迟返回，不应阻塞/抢先快端点）。"""
        await asyncio.sleep(0.3)
        return httpx.Response(200, json={"code": 0, "data": {"id": "node-2"}})

    for url in _candidate_urls(prober):
        port = url.rsplit(":", 1)[1]
        if port == "8000":
            # 首选端点快速失败（连接拒绝）
            respx.post(f"{url}/api/v1/agent-nodes/register").respond(500)
        elif port == "8001":
            # 8001 快速成功（应胜出）
            respx.post(f"{url}/api/v1/agent-nodes/register").respond(
                200, json={"code": 0, "data": {"id": "node-1", "registration_token": "t"}}
            )
        elif port == "8002":
            # 8002 慢速成功（不阻塞快端点胜出）
            respx.post(f"{url}/api/v1/agent-nodes/register").mock(side_effect=_slow_success)
        else:
            respx.post(f"{url}/api/v1/agent-nodes/register").respond(500)

    node_data = await prober.register()

    assert prober.active_api_url == f"http://{_HUB_HOST}:8001"
    assert node_data["id"] == "node-1"


@respx.mock
async def test_probe_timeout_does_not_block_fast_winner(
    prober: EndpointProber,
) -> None:
    """UT-2：挂起端点 1s 超时不阻塞快端点胜出（总耗时远小于超时×候选数）。"""

    async def _hang(request: httpx.Request) -> httpx.Response:
        """永久挂起的探测端点（由竞速取消/超时回收）。"""
        await asyncio.Event().wait()  # pragma: no cover - 被取消后不可达
        return httpx.Response(500)  # pragma: no cover

    for url in _candidate_urls(prober):
        port = url.rsplit(":", 1)[1]
        if port == "8000":
            respx.post(f"{url}/api/v1/agent-nodes/register").mock(side_effect=_hang)
        elif port == "8010":
            respx.post(f"{url}/api/v1/agent-nodes/register").respond(
                200, json={"code": 0, "data": {"id": "node-10"}}
            )
        else:
            respx.post(f"{url}/api/v1/agent-nodes/register").respond(500)

    start = time.monotonic()
    await prober.register()
    elapsed = time.monotonic() - start

    assert prober.active_api_url == f"http://{_HUB_HOST}:8010"
    # 快端点胜出：总耗时≈单次超时上限内，而非 11 × 超时的串行总时长
    assert elapsed < PROBE_TIMEOUT_SECONDS * 5


@respx.mock
async def test_race_all_failed_raises_last_error(
    prober: EndpointProber,
) -> None:
    """UT-1（后半）：全部候选失败时抛出异常，由重连循环按间隔重试整环。"""
    for url in _candidate_urls(prober):
        respx.post(f"{url}/api/v1/agent-nodes/register").respond(500)

    with pytest.raises(Exception):
        await prober.register()

    assert prober.active_api_url == f"http://{_HUB_HOST}:8000"  # 未锁定


@respx.mock
async def test_locked_endpoint_registers_directly(
    prober: EndpointProber,
) -> None:
    """锁定端点时跳过竞速，直接对锁定端点常规注册。"""
    prober.lock(f"http://{_HUB_HOST}:8003")
    route = respx.post(f"http://{_HUB_HOST}:8003/api/v1/agent-nodes/register").respond(
        200, json={"code": 0, "data": {"id": "node-3"}}
    )

    node_data = await prober.register()

    assert node_data["id"] == "node-3"
    assert route.called
    assert prober.active_api_url == f"http://{_HUB_HOST}:8003"
