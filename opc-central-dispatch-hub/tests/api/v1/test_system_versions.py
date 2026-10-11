"""系统版本 API 测试。"""

import datetime
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.models.base import AsyncSessionLocal
from app.models.system_version import SystemVersion

VERSION_A = "v0.1.0"
VERSION_B = "v0.2.0"


async def _create_real_session() -> AsyncSession:
    """创建绑定独立 engine 的会话。

    pytest-asyncio 为每个用例创建新事件循环，若直接复用全局 engine 的
    连接池，asyncpg 连接会跨 loop 报 "attached to a different loop"。
    参照 test_settings.py 的做法，每次新建 engine 并在使用后释放。
    """
    engine = create_async_engine(get_settings().database_url, echo=False)
    return AsyncSessionLocal(bind=engine)


async def _cleanup_versions() -> None:
    """清理 system_versions 表全部数据。"""
    session = await _create_real_session()
    try:
        async with session:
            await session.execute(delete(SystemVersion))
            await session.commit()
    finally:
        await session.bind.dispose()


async def _insert_version(
    version: str,
    release_date: datetime.date,
    is_current: bool = False,
) -> SystemVersion:
    """插入一条版本记录并提交。"""
    record = SystemVersion(
        id=uuid.uuid4(),
        version=version,
        title=f"{version} 标题",
        content=f"{version} 更新内容",
        release_date=release_date,
        is_current=is_current,
    )
    session = await _create_real_session()
    try:
        async with session:
            session.add(record)
            await session.commit()
    finally:
        await session.bind.dispose()
    return record


@pytest.fixture(autouse=True)
async def _clean_versions() -> None:
    """每个用例前后清理版本表，并在结束后恢复进入前的数据快照。

    ADR-026 起 system_versions 承载「当前运行版本号」（侧边栏与检查更新基线），
    不可被测试清空丢失：进入前快照现有记录，结束后删除测试残留并按快照恢复。
    """
    session = await _create_real_session()
    snapshot: list[SystemVersion] = []
    try:
        async with session:
            result = await session.execute(select(SystemVersion))
            snapshot = list(result.scalars().all())
    finally:
        await session.bind.dispose()

    await _cleanup_versions()
    yield
    await _cleanup_versions()

    # 按快照恢复（先插非当前记录，避免 is_current 部分唯一索引冲突）
    if snapshot:
        restore = await _create_real_session()
        try:
            async with restore:
                for record in sorted(snapshot, key=lambda r: r.is_current):
                    restore.add(
                        SystemVersion(
                            version=record.version,
                            title=record.title,
                            content=record.content,
                            release_date=record.release_date,
                            is_current=record.is_current,
                        )
                    )
                await restore.commit()
        finally:
            await restore.bind.dispose()


async def test_list_versions_empty(ws_client) -> None:
    """空表返回空列表。"""
    response = ws_client.get("/api/v1/settings/versions")

    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    assert data["data"] == []


async def test_list_versions_ordered_desc(ws_client) -> None:
    """返回按发布日期倒序排列的版本列表。"""
    await _insert_version(VERSION_A, datetime.date(2026, 9, 1), is_current=True)
    await _insert_version(VERSION_B, datetime.date(2026, 9, 2), is_current=False)

    response = ws_client.get("/api/v1/settings/versions")

    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    items = data["data"]
    assert len(items) == 2
    assert items[0]["version"] == VERSION_B
    assert items[1]["version"] == VERSION_A
    first = items[0]
    assert set(first.keys()) == {
        "id",
        "version",
        "title",
        "content",
        "release_date",
        "is_current",
    }


async def test_list_versions_current_marker(ws_client) -> None:
    """有且仅有一条 is_current=true 记录。"""
    await _insert_version(VERSION_A, datetime.date(2026, 9, 1), is_current=True)
    await _insert_version(VERSION_B, datetime.date(2026, 9, 2), is_current=False)

    response = ws_client.get("/api/v1/settings/versions")

    items = response.json()["data"]
    current_items = [item for item in items if item["is_current"]]
    assert len(current_items) == 1
    assert current_items[0]["version"] == VERSION_A


async def test_partial_unique_index_rejects_second_current() -> None:
    """部分唯一索引：未释放旧当前版本时插入第二条 is_current=true 被拒绝。"""
    await _insert_version(VERSION_A, datetime.date(2026, 9, 1), is_current=True)

    with pytest.raises(IntegrityError):
        await _insert_version(VERSION_B, datetime.date(2026, 9, 2), is_current=True)

    await _cleanup_versions()


async def test_version_unique_index_rejects_duplicate() -> None:
    """唯一索引：重复 version 被拒绝。"""
    await _insert_version(VERSION_A, datetime.date(2026, 9, 1), is_current=True)

    with pytest.raises(IntegrityError):
        await _insert_version(VERSION_A, datetime.date(2026, 9, 3), is_current=False)
