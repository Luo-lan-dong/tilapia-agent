"""SessionStore 单元测试。"""

import json
from pathlib import Path

import pytest

from app.executor.session_store import SessionStore


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    """返回位于临时目录（父目录尚不存在）下的登记簿文件路径。"""
    return tmp_path / "data" / "sessions.json"


async def test_upsert_then_get(store_path: Path) -> None:
    """新建记录后 get 返回一致（含时间字段往返序列化）。"""
    store = SessionStore(store_path)
    record = await store.upsert(
        "task-1",
        session_id="sess-1",
        workspace_dir="/workspace/task-1",
        status="running",
    )
    assert record.task_id == "task-1"

    got = await store.get("task-1")
    assert got == record


async def test_get_missing_returns_none(store_path: Path) -> None:
    """文件不存在时查询任意 task_id 返回 None。"""
    store = SessionStore(store_path)
    assert await store.get("task-missing") is None


async def test_upsert_existing_keeps_created_at(store_path: Path) -> None:
    """upsert 更新已有记录不丢字段：created_at 保留，传入字段被覆盖。"""
    store = SessionStore(store_path)
    first = await store.upsert(
        "task-1",
        session_id="sess-1",
        workspace_dir="/workspace/task-1",
        status="running",
    )
    second = await store.upsert(
        "task-1",
        session_id="sess-2",
        workspace_dir="/workspace/task-1-v2",
        status="completed",
    )
    assert second.session_id == "sess-2"
    assert second.workspace_dir == "/workspace/task-1-v2"
    assert second.status == "completed"
    assert second.created_at == first.created_at
    assert second.updated_at >= first.updated_at


async def test_update_status(store_path: Path) -> None:
    """update_status 修改状态并落盘。"""
    store = SessionStore(store_path)
    await store.upsert(
        "task-1",
        session_id="sess-1",
        workspace_dir="/workspace/task-1",
        status="running",
    )
    updated = await store.update_status("task-1", "completed")
    assert updated is not None
    assert updated.status == "completed"
    # 新实例读取验证已落盘
    got = await SessionStore(store_path).get("task-1")
    assert got is not None
    assert got.status == "completed"


async def test_update_status_missing_returns_none(store_path: Path) -> None:
    """update_status 目标不存在时返回 None。"""
    store = SessionStore(store_path)
    assert await store.update_status("task-missing", "completed") is None


async def test_upsert_after_corrupted_json(store_path: Path) -> None:
    """登记簿 JSON 损坏时记录 warning 并当空表重建，upsert 正常工作。"""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text("{ 损坏的 JSON !!!", encoding="utf-8")

    store = SessionStore(store_path)
    record = await store.upsert(
        "task-1",
        session_id="sess-1",
        workspace_dir="/workspace/task-1",
        status="running",
    )
    assert record.task_id == "task-1"

    got = await store.get("task-1")
    assert got is not None
    assert got.session_id == "sess-1"
    # 文件已被重建为合法 JSON
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert "task-1" in data
