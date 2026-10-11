"""Agent 模板服务与端点测试。"""

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.services.agent_template_service import AgentTemplateService


async def _noop_background_loop(stop_event: asyncio.Event) -> None:
    """占位后台循环，避免真实调度器/心跳任务占用事件循环。"""
    await stop_event.wait()


def _noop_start_background_task() -> tuple[asyncio.Event, asyncio.Task[None]]:
    """创建占位后台任务，用于替换真实的 lifespan 后台任务。"""
    stop_event = asyncio.Event()
    task = asyncio.create_task(_noop_background_loop(stop_event))
    return stop_event, task


@pytest_asyncio.fixture
async def http_client() -> AsyncGenerator[AsyncClient, None]:
    """创建 HTTP 级异步测试客户端。"""
    with (
        patch("app.main.start_scheduler", _noop_start_background_task),
        patch("app.main.start_heartbeat_monitor", _noop_start_background_task),
    ):
        from app.main import create_app

        app = create_app()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


def test_list_all_templates() -> None:
    """无 agent_type 参数返回所有类型的模板。"""
    service = AgentTemplateService()
    response = service.list_templates()

    programming_versions = [t for t in response.templates if t.agent_type == "programming"]
    versions = sorted(t.version for t in programming_versions)
    # 断言关键版本存在且列表有序，数量随发版增长不做硬编码
    assert {"0.1.0", "0.2.0", "0.3.1"} <= set(versions)
    assert versions == sorted(versions)
    for template in programming_versions:
        assert template.download_path.startswith("/static/agent-templates/programming/")
        assert template.download_path.endswith(".tar.gz")


def test_list_templates_by_agent_type() -> None:
    """指定 agent_type 只返回对应类型的模板。"""
    service = AgentTemplateService()
    response = service.list_templates(agent_type="programming")

    assert all(t.agent_type == "programming" for t in response.templates)
    versions = sorted(t.version for t in response.templates)
    assert {"0.1.0", "0.2.0", "0.3.1"} <= set(versions)


def test_list_templates_nonexistent_type() -> None:
    """不存在的 agent_type 返回空列表。"""
    service = AgentTemplateService()
    response = service.list_templates(agent_type="nonexistent")

    assert response.templates == []


async def test_endpoint_list_all_templates(http_client: AsyncClient) -> None:
    """GET /api/v1/agent-templates 无参数返回所有模板。"""
    response = await http_client.get("/api/v1/agent-templates")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    templates = body["data"]["templates"]
    programming = [t for t in templates if t["agent_type"] == "programming"]
    versions = sorted(t["version"] for t in programming)
    assert {"0.1.0", "0.2.0", "0.3.1"} <= set(versions)


async def test_endpoint_list_templates_by_type(
    http_client: AsyncClient,
) -> None:
    """GET /api/v1/agent-templates?agent_type=programming 只返回对应类型。"""
    response = await http_client.get(
        "/api/v1/agent-templates",
        params={"agent_type": "programming"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    templates = body["data"]["templates"]
    assert templates
    assert all(t["agent_type"] == "programming" for t in templates)
