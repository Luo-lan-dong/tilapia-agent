"""开发规则文档 API 测试（ADR-048）。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.models.base import AsyncSessionLocal
from app.models.standard_doc import StandardDoc
from app.services.standard_doc_service import build_rule_docs_payload

DOCS_URL = "/api/v1/docs"


async def _create_real_session() -> AsyncSession:
    """创建绑定独立 engine 的会话（避免跨事件循环复用连接池）。"""
    engine = create_async_engine(get_settings().database_url, echo=False)
    return AsyncSessionLocal(bind=engine)


@pytest.fixture(autouse=True)
async def _clean_docs() -> AsyncGenerator[None, None]:
    """每个用例前后清理文档表（保留预置模板，防止清掉迁移 seed）。"""
    for _phase in range(2):
        session = await _create_real_session()
        try:
            async with session:
                await session.execute(
                    delete(StandardDoc).where(StandardDoc.is_builtin.is_(False))
                )
                await session.commit()
        finally:
            await session.bind.dispose()
        if _phase == 0:
            yield


def _payload(name: str = "test-doc", content: str = "# 正文") -> dict:
    return {
        "name": name,
        "category": "custom",
        "description": "测试文档",
        "apply_when": "每次写/改代码前",
        "content": content,
    }


async def test_doc_crud_flow(ws_client) -> None:
    """创建 → 列表 → 详情 → 更新自增 → 删除 全流程。"""
    response = ws_client.post(DOCS_URL, json=_payload())
    assert response.status_code == 201
    created = response.json()["data"]
    doc_id = created["id"]
    assert created["version"] == 1
    assert created["is_builtin"] is False

    listed = ws_client.get(DOCS_URL)
    assert listed.status_code == 200
    items = listed.json()["data"]["items"]
    created_item = next(item for item in items if item["id"] == doc_id)
    assert "content" not in created_item  # 列表不含正文
    assert created_item["name"] == "test-doc"

    detail = ws_client.get(f"{DOCS_URL}/{doc_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["content"] == "# 正文"

    updated = ws_client.put(f"{DOCS_URL}/{doc_id}", json={"content": "# 新正文"})
    assert updated.status_code == 200
    assert updated.json()["data"]["version"] == 2

    deleted = ws_client.delete(f"{DOCS_URL}/{doc_id}")
    assert deleted.status_code == 200
    assert ws_client.get(f"{DOCS_URL}/{doc_id}").status_code == 404


async def test_create_duplicate_name_409(ws_client) -> None:
    """同名创建返回 409。"""
    assert ws_client.post(DOCS_URL, json=_payload()).status_code == 201
    assert ws_client.post(DOCS_URL, json=_payload()).status_code == 409


async def test_download_invalid_token_401(ws_client) -> None:
    """无效 token 下载返回 401。"""
    response = ws_client.get(f"{DOCS_URL}/download/rule-doc?token=invalid-token")
    assert response.status_code == 401


async def test_download_with_valid_token(ws_client) -> None:
    """有效 token 下载返回 md 内容（快照 → payload → 下载闭环）。"""
    created = ws_client.post(DOCS_URL, json=_payload(content="# 下载正文")).json()["data"]

    session = await _create_real_session()
    try:
        async with session:
            from app.models.task import Task

            doc = await session.get(StandardDoc, uuid.UUID(created["id"]))
            task = Task(
                title="规则文档下载验证",
                agent_type="code",
                specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
                rule_docs=[
                    {
                        "doc_id": str(doc.id),
                        "name": doc.name,
                        "version": doc.version,
                        "apply_when": doc.apply_when,
                        "content": doc.content,
                    }
                ],
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            payload = await build_rule_docs_payload(session, task)
            assert payload is not None
            standard = next(entry for entry in payload if entry["target"] == "standard")
            url = standard["url"]
    finally:
        await session.bind.dispose()

    response = ws_client.get(url)
    assert response.status_code == 200
    assert response.content.decode("utf-8") == "# 下载正文"
