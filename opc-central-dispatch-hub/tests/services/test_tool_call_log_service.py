"""执行链路事件服务测试（ADR-047 UT-3~UT-6 + agent 日志拉取组装）。

record / query / purge 走独立会话，测试通过 monkeypatch
``tool_call_log_service.AsyncSessionLocal`` 为共享会话工厂，使写入与查询
落在同一 savepoint 会话上，测试结束统一回滚，不污染开发库。
"""

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool_call_log import ToolCallLog
from app.services import tool_call_log_service


class _SharedSessionCM:
    """把共享会话包成可重复进入的 async context manager。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest_asyncio.fixture
async def shared_session_factory(monkeypatch, db_session: AsyncSession) -> AsyncSession:
    """把服务内部的 AsyncSessionLocal 替换为 db_session 工厂（savepoint 模式）。"""
    monkeypatch.setattr(
        tool_call_log_service,
        "AsyncSessionLocal",
        lambda: _SharedSessionCM(db_session),
    )
    # 恢复真实 record（conftest autouse 已替换为 spy，原实现存于 _real_record）
    monkeypatch.setattr(
        tool_call_log_service, "record", tool_call_log_service._real_record
    )
    # 测试隔离：清掉开发库残留行（历史测试中断可能提交真实行），savepoint 内删除
    await db_session.execute(delete(ToolCallLog))
    return db_session


async def test_record_truncates_and_snapshots(shared_session_factory: AsyncSession) -> None:
    """UT-3：字段截断 + request_id 缺省快照（contextvars 无绑定则 None）。"""
    await tool_call_log_service.record(
        kind="skill_call",
        name="find_idle_nodes",
        status="started",
        session_id="ses-123",
        task_id="task-456",
        args_summary="a" * 3000,
        result_summary="r" * 5000,
        error=None,
        duration_ms=12,
    )
    result = await shared_session_factory.execute(select(ToolCallLog))
    rows = list(result.scalars().all())
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "skill_call"
    assert len(row.args_summary) == 2000
    assert len(row.result_summary) == 4000
    assert row.session_id == "ses-123"
    assert row.duration_ms == 12


async def test_record_failure_swallowed(monkeypatch) -> None:
    """UT-4：写库异常仅告警不抛出。"""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def broken_factory():
        raise RuntimeError("db down")
        yield None  # pragma: no cover

    monkeypatch.setattr(tool_call_log_service, "AsyncSessionLocal", broken_factory)
    monkeypatch.setattr(
        tool_call_log_service, "record", tool_call_log_service._real_record
    )
    # 不抛异常即通过
    await tool_call_log_service.record(kind="llm_call", name="m1")


async def test_query_events_filters(shared_session_factory: AsyncSession) -> None:
    """UT-5：kind/status/关键字过滤与倒序。"""
    await tool_call_log_service.record(kind="llm_call", name="model-a", duration_ms=10)
    await tool_call_log_service.record(
        kind="skill_call", name="find_idle_nodes", status="started", args_summary="查节点"
    )
    await tool_call_log_service.record(
        kind="skill_result", name="find_idle_nodes", status="failed", error="超时"
    )
    rows = await tool_call_log_service.query_events()
    assert len(rows) == 3
    assert rows[0].kind == "skill_result"  # 时间倒序

    failed = await tool_call_log_service.query_events(statuses={"failed"})
    assert len(failed) == 1
    assert failed[0].kind == "skill_result"

    keyword_rows = await tool_call_log_service.query_events(keyword="查节点")
    assert len(keyword_rows) == 1
    assert keyword_rows[0].args_summary == "查节点"

    kinds_rows = await tool_call_log_service.query_events(kinds={"llm_call"})
    assert len(kinds_rows) == 1


async def test_fetch_trace_requires_identifier() -> None:
    """UT-5：无 request_id/session_id 返回空列表。"""
    assert await tool_call_log_service.fetch_trace() == []


async def test_fetch_trace_ascending(shared_session_factory: AsyncSession) -> None:
    """UT-5：按 session_id 拉链路，时间升序。"""
    await tool_call_log_service.record(
        kind="llm_call", name="m1", session_id="ses-trace", request_id="req-t"
    )
    await tool_call_log_service.record(
        kind="skill_call", name="find_idle_nodes", session_id="ses-trace", request_id="req-t"
    )
    await tool_call_log_service.record(
        kind="llm_call", name="m2", session_id="ses-other", request_id="req-x"
    )
    rows = await tool_call_log_service.fetch_trace(session_id="ses-trace")
    assert len(rows) == 2
    assert rows[0].kind == "llm_call"
    assert rows[1].kind == "skill_call"
    by_request = await tool_call_log_service.fetch_trace(request_id="req-t")
    assert len(by_request) == 2


async def test_purge_expired(shared_session_factory: AsyncSession) -> None:
    """UT-6：超期行删除、保留期内行保留。"""
    await tool_call_log_service.record(kind="llm_call", name="keep-me")
    old_row = ToolCallLog(kind="llm_call", name="old-row", created_at=datetime.now(UTC) - timedelta(days=95))
    shared_session_factory.add(old_row)
    await shared_session_factory.commit()

    deleted = await tool_call_log_service.purge_expired(90)
    assert deleted >= 1
    rows = await tool_call_log_service.query_events(keyword="old-row")
    assert rows == []
    keep = await tool_call_log_service.query_events(keyword="keep-me")
    assert len(keep) == 1


async def test_log_chunk_assembly() -> None:
    """UT-9：分块按 seq 拼接并解码；done 触发 Future 完成（乱序容忍）。"""
    content = "第一行日志\n第二行 ERROR 日志\n"
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    request_id = "req-assembly"
    future = tool_call_log_service.register_log_fetch(request_id)
    # 先发后块（未 done），再发首块（done）——拼接按 seq 排序
    tool_call_log_service.handle_log_chunk(request_id, seq=1, total=2, data_b64=encoded[4:], done=False)
    tool_call_log_service.handle_log_chunk(request_id, seq=0, total=2, data_b64=encoded[:4], done=True)
    result = await future
    assert result == content


async def test_log_chunk_incomplete_transfers_error() -> None:
    """UT-9：done 时分块不全 → Future 以异常完成（断线截断防御）。"""
    request_id = "req-incomplete"
    future = tool_call_log_service.register_log_fetch(request_id)
    tool_call_log_service.handle_log_chunk(request_id, seq=0, total=3, data_b64="AAAA", done=True)
    with pytest.raises(RuntimeError, match="分块不完整"):
        await future


async def test_log_chunk_error_completes_future_with_exception() -> None:
    """UT-9：agent.log_error 使等待 Future 以异常完成。"""
    request_id = "req-error"
    future = tool_call_log_service.register_log_fetch(request_id)
    tool_call_log_service.handle_log_error(request_id, "文件不存在")
    with pytest.raises(RuntimeError, match="文件不存在"):
        await future


async def test_log_chunk_cancel_cleans_up() -> None:
    """UT-9：超时清理后注册表无残留。"""
    request_id = "req-cancel"
    future = tool_call_log_service.register_log_fetch(request_id)
    tool_call_log_service.cancel_log_fetch(request_id)
    assert request_id not in tool_call_log_service._log_fetch_waiters
    assert future.cancelled()
