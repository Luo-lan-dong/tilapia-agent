"""日志查询端点测试（ADR-047 UT-7/UT-8/UT-9）。

不触库：GET /logs（memory/file）、trace 参数校验、agent-fetch 离线拒绝。
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


def _noop_start_background_task() -> tuple[asyncio.Event, asyncio.Task[None]]:
    stop_event = asyncio.Event()
    return stop_event, asyncio.create_task(_wait_forever(stop_event))


async def _wait_forever(stop_event: asyncio.Event) -> None:
    await stop_event.wait()


def _noop_start_reverse_notify() -> asyncio.Task[None]:
    return asyncio.create_task(asyncio.sleep(0))


@pytest.fixture
async def http_client() -> AsyncGenerator[AsyncClient, None]:
    with (
        patch("app.main.start_scheduler", _noop_start_background_task),
        patch("app.main.start_heartbeat_monitor", _noop_start_background_task),
        patch("app.main.start_reverse_notify", _noop_start_reverse_notify),
    ):
        from app.core.logging import configure_logging

        configure_logging(environment="dev", log_level="INFO", ring_buffer_size=100)
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


async def test_query_logs_memory_default(http_client: AsyncClient) -> None:
    """UT-7：默认内存数据源返回 200 与列表；日志条目进缓冲后可查。"""
    logging.getLogger("logs_api_test").error("测试错误条目 unique-keyword-921")
    resp = await http_client.get("/api/v1/logs")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "memory"
    assert isinstance(data["entries"], list)
    assert any("unique-keyword-921" in (e["message"] or "") for e in data["entries"])


async def test_query_logs_level_and_keyword_filter(http_client: AsyncClient) -> None:
    """UT-7：级别多值与关键字过滤。"""
    logging.getLogger("logs_api_filter_test").warning("warn-entry-abc")
    resp = await http_client.get(
        "/api/v1/logs", params={"level": ["WARNING"], "keyword": "warn-entry-abc"}
    )
    assert resp.status_code == 200
    entries = resp.json()["data"]["entries"]
    assert entries
    assert all(e["level"] == "WARNING" for e in entries)


async def test_query_logs_invalid_time(http_client: AsyncClient) -> None:
    """UT-7：非法时间参数 422。"""
    resp = await http_client.get("/api/v1/logs", params={"start": "not-a-date"})
    assert resp.status_code == 422


async def test_query_logs_source_file(http_client: AsyncClient) -> None:
    """UT-7：file 数据源返回 200（目录无文件时返回空列表）。"""
    resp = await http_client.get("/api/v1/logs", params={"source": "file", "limit": 10})
    assert resp.status_code == 200
    assert resp.json()["data"]["source"] == "file"


async def test_trace_recent_without_identifier(http_client: AsyncClient) -> None:
    """UT-8：无链路 ID 时返回最近事件列表（mode=recent，200）。"""
    resp = await http_client.get("/api/v1/logs/trace")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["mode"] == "recent"
    assert isinstance(body["data"]["events"], list)


async def test_ring_buffer_stats(http_client: AsyncClient) -> None:
    """环形缓冲规模端点。"""
    resp = await http_client.get("/api/v1/logs/ring-buffer/stats")
    assert resp.status_code == 200
    assert "size" in resp.json()["data"]


async def test_agent_fetch_offline_node(http_client: AsyncClient) -> None:
    """UT-9：节点不在线时 409，不下发指令。"""
    resp = await http_client.post(
        "/api/v1/logs/agent-fetch",
        json={"client_id": "nonexistent-node", "tail_lines": 100},
    )
    assert resp.status_code == 409
