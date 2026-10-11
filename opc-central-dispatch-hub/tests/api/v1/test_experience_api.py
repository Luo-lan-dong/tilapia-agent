"""经验记忆管理 API 测试（ADR-050）。"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine

from app.models.experience_record import ExperienceRecord
from app.services.experience_service import ExperienceService

EXPERIENCE_URL = "/api/v1/experience"


@pytest.fixture(autouse=True)
async def _clean_records() -> AsyncGenerator[None, None]:
    """每个用例前后清空 experience_records（独立 engine，避免跨事件循环复用连接池）。"""
    from app.core.config import get_settings
    from app.models.base import AsyncSessionLocal

    for _phase in range(2):
        engine = create_async_engine(get_settings().database_url, echo=False)
        try:
            session = AsyncSessionLocal(bind=engine)
            async with session:
                await session.execute(delete(ExperienceRecord))
                await session.commit()
        finally:
            await engine.dispose()
        if _phase == 0:
            yield


def _seed_one(tool: str = "HtmlProjectInfo", resolved: bool = False) -> str:
    """独立事件循环写入一条经验条目，返回 id（TestClient 循环之外安全操作）。"""
    from app.core.config import get_settings
    from app.models.base import AsyncSessionLocal

    async def _run() -> str:
        engine = create_async_engine(get_settings().database_url, echo=False)
        try:
            session = AsyncSessionLocal(bind=engine)
            async with session:
                service = ExperienceService()
                record = await service.record_failure(
                    session,
                    tool=tool,
                    error_msg="未找到该网站 1.2.3.4",
                    session_id="sess-seed",
                )
                assert record is not None
                if resolved:
                    await service.resolve(
                        session,
                        record.id,
                        root_cause="静态站不属于网站(PHP)模块",
                        solution="改用 HtmlProjectInfo(site_name=...)",
                    )
                return str(record.id)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def test_list_and_get_and_404(ws_client) -> None:
    """UT-EP1：列表/详情/404（208001）。"""
    listed = ws_client.get(EXPERIENCE_URL)
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 0

    record_id = _seed_one()

    listed = ws_client.get(EXPERIENCE_URL)
    assert listed.json()["data"]["total"] == 1
    assert listed.json()["data"]["items"][0]["tool"] == "HtmlProjectInfo"

    detail = ws_client.get(f"{EXPERIENCE_URL}/{record_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "pending"

    missing = ws_client.get(f"{EXPERIENCE_URL}/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["code"] == 208001


def test_list_keyword_and_status_filter(ws_client) -> None:
    """UT-EP1b：keyword 搜索与 status 过滤。"""
    _seed_one(tool="HtmlProjectInfo")
    _seed_one(tool="SiteDelete", resolved=True)

    by_keyword = ws_client.get(f"{EXPERIENCE_URL}?keyword=Site")
    assert by_keyword.json()["data"]["total"] == 1
    assert by_keyword.json()["data"]["items"][0]["tool"] == "SiteDelete"

    by_status = ws_client.get(f"{EXPERIENCE_URL}?status=resolved")
    assert by_status.json()["data"]["total"] == 1
    assert by_status.json()["data"]["items"][0]["status"] == "resolved"


def test_patch_update_and_resolve(ws_client) -> None:
    """UT-EP2：PATCH 编辑回填；非法 status 422。"""
    record_id = _seed_one()

    response = ws_client.patch(
        f"{EXPERIENCE_URL}/{record_id}",
        json={"root_cause": "根因", "solution": "解法", "status": "resolved"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "resolved"
    assert data["root_cause"] == "根因"
    assert data["resolved_at"] is not None

    invalid = ws_client.patch(f"{EXPERIENCE_URL}/{record_id}", json={"status": "bogus"})
    assert invalid.status_code == 422


def test_delete(ws_client) -> None:
    """UT-EP3：删除后再查 208001。"""
    record_id = _seed_one()

    removed = ws_client.delete(f"{EXPERIENCE_URL}/{record_id}")
    assert removed.status_code == 200
    missing = ws_client.get(f"{EXPERIENCE_URL}/{record_id}")
    assert missing.status_code == 404
    assert missing.json()["code"] == 208001
