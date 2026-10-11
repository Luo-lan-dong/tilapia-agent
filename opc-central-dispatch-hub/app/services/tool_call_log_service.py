"""执行链路事件采集与查询服务（ADR-047）。

- ``record``：写一条执行链路事件（独立会话，失败仅告警不影响业务）；
- ``query_events`` / ``fetch_trace``：链路查询（按 kind/时间/任务过滤，或按
  request_id/session_id 拉全链时间升序）；
- ``purge_expired``：90 天保留清理（调度器每日调用）；
- ``register_log_fetch`` 等：子 Agent 日志按需拉取的等待/组装注册表
  （hub.log_fetch 下行 → agent.log_chunk 回传 → Future 完成）。
"""

import asyncio
import base64
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, or_, select

from app.models.base import AsyncSessionLocal
from app.models.tool_call_log import ToolCallLog

logger = logging.getLogger(__name__)

#: 快照字段截断上限（ADR-047：防链路表膨胀）
ARGS_SUMMARY_MAX = 2000
RESULT_SUMMARY_MAX = 4000
ERROR_MAX = 4000
NAME_MAX = 128
#: 单次链路查询返回行数上限
TRACE_MAX_ROWS = 500

#: 日志回传事件名（与 ws_messages 常量对应；避免循环 import 此处独立声明）
_LOG_CHUNK_TOTAL_MAX = 4096


def _truncate(value: Any, limit: int) -> str | None:
    """把任意值转为截断后的 JSON 文本摘要。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value[:limit]
    try:
        import json

        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except (TypeError, ValueError):
        return str(value)[:limit]


def _snapshot_request_id() -> str | None:
    """读取 structlog contextvars 中绑定的 request_id。"""
    from structlog import contextvars

    value = contextvars.get_contextvars().get("request_id")
    return str(value)[:64] if value else None


async def record(
    *,
    kind: str,
    name: str,
    status: str = "success",
    request_id: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    args_summary: Any = None,
    result_summary: Any = None,
    error: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """写入一条执行链路事件（独立会话）。

    采集失败仅记告警日志，绝不向上抛——日志采集永不影响业务主流程。
    request_id 缺省时从 structlog contextvars 快照（与运行日志同源）。
    """
    try:
        row = ToolCallLog(
            kind=kind,
            name=str(name)[:NAME_MAX],
            status=status,
            request_id=(request_id or _snapshot_request_id()) or None,
            session_id=str(session_id)[:64] if session_id else None,
            task_id=str(task_id)[:64] if task_id else None,
            args_summary=_truncate(args_summary, ARGS_SUMMARY_MAX),
            result_summary=_truncate(result_summary, RESULT_SUMMARY_MAX),
            error=_truncate(error, ERROR_MAX),
            duration_ms=duration_ms,
        )
        async with AsyncSessionLocal() as local_session:
            local_session.add(row)
            await local_session.commit()
    except Exception:  # noqa: BLE001 — 采集兜底
        logger.warning(
            "tool_call_log_write_failed",
            extra={
                "event": "tool_call_log_write_failed",
                "event_name": name,
                "log_kind": kind,
            },
            exc_info=True,
        )


async def query_events(
    start: datetime | None = None,
    end: datetime | None = None,
    kinds: set[str] | None = None,
    statuses: set[str] | None = None,
    task_id: str | None = None,
    keyword: str | None = None,
    limit: int = 200,
) -> list[ToolCallLog]:
    """按条件查询执行链路事件（时间倒序）。"""
    stmt = select(ToolCallLog)
    if start:
        stmt = stmt.where(ToolCallLog.created_at >= start)
    if end:
        stmt = stmt.where(ToolCallLog.created_at <= end)
    if kinds:
        stmt = stmt.where(ToolCallLog.kind.in_(kinds))
    if statuses:
        stmt = stmt.where(ToolCallLog.status.in_(statuses))
    if task_id:
        stmt = stmt.where(ToolCallLog.task_id == task_id)
    if keyword:
        pattern = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                ToolCallLog.name.ilike(pattern),
                ToolCallLog.args_summary.ilike(pattern),
                ToolCallLog.result_summary.ilike(pattern),
                ToolCallLog.error.ilike(pattern),
            )
        )
    stmt = stmt.order_by(ToolCallLog.created_at.desc()).limit(min(limit, 1000))
    async with AsyncSessionLocal() as session:
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def fetch_trace(
    request_id: str | None = None,
    session_id: str | None = None,
    kinds: set[str] | None = None,
    limit: int = TRACE_MAX_ROWS,
) -> list[ToolCallLog]:
    """按 request_id 或 session_id 拉取执行链路（时间升序，溯源入口）。"""
    stmt = select(ToolCallLog)
    if request_id:
        stmt = stmt.where(ToolCallLog.request_id == request_id)
    elif session_id:
        stmt = stmt.where(ToolCallLog.session_id == session_id)
    else:
        return []
    if kinds:
        stmt = stmt.where(ToolCallLog.kind.in_(kinds))
    stmt = stmt.order_by(ToolCallLog.created_at.asc()).limit(min(limit, TRACE_MAX_ROWS))
    async with AsyncSessionLocal() as session:
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def purge_expired(retention_days: int) -> int:
    """清理超过保留期的链路事件，返回删除行数（调度器每日调用）。"""
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    async with AsyncSessionLocal() as session:
        result = await session.execute(delete(ToolCallLog).where(ToolCallLog.created_at < cutoff))
        await session.commit()
        return result.rowcount or 0


def serialize_event(row: ToolCallLog) -> dict[str, Any]:
    """把链路事件行序列化为响应字典。"""
    return {
        "id": str(row.id),
        "request_id": row.request_id,
        "session_id": row.session_id,
        "task_id": row.task_id,
        "kind": row.kind,
        "name": row.name,
        "status": row.status,
        "args_summary": row.args_summary,
        "result_summary": row.result_summary,
        "error": row.error,
        "duration_ms": row.duration_ms,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ---------------------------------------------------------------------------
# 子 Agent 日志按需拉取（hub.log_fetch → agent.log_chunk 回传组装）
# ---------------------------------------------------------------------------

_log_fetch_waiters: dict[str, asyncio.Future[str]] = {}
_log_fetch_chunks: dict[str, dict[int, str]] = {}


def register_log_fetch(request_id: str) -> asyncio.Future[str]:
    """注册一次节点日志拉取等待（返回 Future，由端点 await）。"""
    _log_fetch_chunks.pop(request_id, None)
    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    _log_fetch_waiters[request_id] = future
    return future


def handle_log_chunk(request_id: str, seq: int, total: int, data_b64: str, done: bool) -> None:
    """接收一个日志分块；``done`` 时按 seq 拼接并完成等待 Future。"""
    if total > _LOG_CHUNK_TOTAL_MAX:
        handle_log_error(request_id, "分块数超限")
        return
    chunks = _log_fetch_chunks.setdefault(request_id, {})
    chunks[seq] = data_b64
    if done:
        _log_fetch_chunks.pop(request_id, None)
        future = _log_fetch_waiters.get(request_id)
        if future is not None and not future.done():
            if len(chunks) < total:
                # 分块丢失（断线截断）：转错误而非解码半截内容
                future.set_exception(
                    RuntimeError(f"日志分块不完整（{len(chunks)}/{total}），节点可能刚断线")
                )
                return
            ordered = "".join(chunks[key] for key in sorted(chunks))
            try:
                future.set_result(base64.b64decode(ordered).decode("utf-8", errors="replace"))
            except Exception as exc:  # noqa: BLE001 — 解码失败转错误
                future.set_exception(exc)


def handle_log_error(request_id: str, reason: str) -> None:
    """节点回传失败：完成（异常）等待 Future。"""
    _log_fetch_chunks.pop(request_id, None)
    future = _log_fetch_waiters.get(request_id)
    if future is not None and not future.done():
        future.set_exception(RuntimeError(reason))


def cancel_log_fetch(request_id: str) -> None:
    """拉取等待清理（端点超时/取消时调用，防注册表泄漏）。"""
    _log_fetch_chunks.pop(request_id, None)
    future = _log_fetch_waiters.pop(request_id, None)
    if future is not None and not future.done():
        future.cancel()


class NodeLogFetchError(Exception):
    """节点日志拉取失败（端点与技能共用；message 面向展示）。

    code：offline（节点不在线）/ send_failed（指令下发失败）/
    timeout（回传超时）/ agent_error（节点回传错误）。
    """

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


async def request_node_logs(
    client_id: str,
    tail_lines: int = 200,
    level: str | None = None,
    timeout_seconds: float | None = None,
) -> str:
    """向在线节点下发 ``hub.log_fetch`` 并同步等待回传文本。

    REST 端点（POST /logs/agent-fetch）与技能（fetch_node_logs）共用本函数；
    失败抛 :class:`NodeLogFetchError`，调用方按 code 映射各自的错误形态。
    """
    from app.core.config import get_settings
    from app.services.connection_manager import connection_manager

    settings = get_settings()
    if not connection_manager.is_connected(client_id):
        raise NodeLogFetchError("节点不在线，无法拉取日志", "offline")
    tail = max(1, min(tail_lines, settings.agent_log_fetch_max_lines))
    request_id = f"logfetch-{client_id[:8]}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
    future = register_log_fetch(request_id)
    sent = await connection_manager.send_to_node(
        client_id,
        {
            "event": "hub.log_fetch",
            "payload": {"request_id": request_id, "tail_lines": tail, "level": level},
            "request_id": request_id,
        },
    )
    if not sent:
        cancel_log_fetch(request_id)
        raise NodeLogFetchError("指令下发失败，节点可能刚断线", "send_failed")
    wait_seconds = (
        timeout_seconds if timeout_seconds is not None else settings.agent_log_fetch_timeout_seconds
    )
    try:
        return await asyncio.wait_for(future, timeout=wait_seconds)
    except TimeoutError:
        cancel_log_fetch(request_id)
        raise NodeLogFetchError(
            "节点日志回传超时（节点可能为旧版本或日志文件过大）", "timeout"
        ) from None
    except Exception as exc:  # noqa: BLE001 — 节点回传错误经 Future 异常透出
        cancel_log_fetch(request_id)
        raise NodeLogFetchError(f"节点日志回传失败：{exc}", "agent_error") from exc
