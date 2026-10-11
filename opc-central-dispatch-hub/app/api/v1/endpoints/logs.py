"""日志查询端点（ADR-047）。

- ``GET /api/v1/logs``：运行日志查询（数据源=内存环形缓冲或当前文件尾部）；
- ``GET /api/v1/logs/trace``：执行链路溯源（按 request_id/session_id 拉
  tool_call_logs 全链，时间升序）；
- ``POST /api/v1/logs/agent-fetch``：子 Agent 节点日志按需拉取
  （下发 hub.log_fetch → 同步等待 agent.log_chunk 回传 → 返回文本）。
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import read_ring_buffer, ring_buffer_size
from app.schemas.common import ApiResponse
from app.services import tool_call_log_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["logs"])

#: 日志文件最大回读行数（防一次性载入超大文件）
_FILE_TAIL_MAX_LINES = 5000


def _read_file_tail(log_dir: str, lines: int) -> list[dict[str, Any]]:
    """读取当前 app.log 尾部若干行的轻量解析（非 JSON 行原样返回 message）。"""
    path = Path(log_dir) / "app.log"
    if not path.is_file():
        return []
    lines = min(max(lines, 1), _FILE_TAIL_MAX_LINES)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            tail = fh.readlines()[-lines:]
    except OSError:
        return []
    parsed: list[dict[str, Any]] = []
    for raw in reversed(tail):
        text = raw.rstrip("\n")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            parsed.append({"message": text})
            continue
        parsed.append(
            {
                "timestamp": data.get("timestamp"),
                "level": data.get("level"),
                "logger": data.get("logger"),
                "message": data.get("message") or data.get("event"),
                "request_id": data.get("request_id"),
                "metadata": data,
            }
        )
    return parsed


class LogEntryResponse(BaseModel):
    """运行日志条目。"""

    timestamp: str | None = None
    level: str | None = None
    logger: str | None = None
    message: str | None = None
    request_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LogsResponse(BaseModel):
    """运行日志查询响应。"""

    entries: list[LogEntryResponse]
    total: int
    source: str


class TraceEventResponse(BaseModel):
    """执行链路事件。"""

    id: str
    request_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    kind: str
    name: str
    status: str
    args_summary: str | None = None
    result_summary: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    created_at: str | None = None


class TraceResponse(BaseModel):
    """执行链路溯源响应。

    mode=recent：未指定链路 ID 时的最近事件列表（时间倒序，概览）；
    mode=trace：按 request_id/session_id 溯源的全链（时间升序，瀑布）。
    """

    events: list[TraceEventResponse]
    total: int
    mode: str = "trace"


class AgentLogFetchRequest(BaseModel):
    """节点日志拉取请求。"""

    client_id: str = Field(..., min_length=1, description="节点 client_id")
    tail_lines: int = Field(default=200, ge=1, le=10000, description="回传最近行数")
    level: str | None = Field(default=None, description="级别过滤（debug/info/warning/error）")


class AgentLogFetchResponse(BaseModel):
    """节点日志拉取响应。"""

    client_id: str
    content: str


@router.get("", response_model=ApiResponse[LogsResponse])
async def query_logs(
    start: Annotated[str | None, Query(description="起始时间 ISO 8601")] = None,
    end: Annotated[str | None, Query(description="结束时间 ISO 8601")] = None,
    level: Annotated[
        list[str] | None, Query(description="级别过滤，可多值（INFO/WARNING/ERROR/DEBUG）")
    ] = None,
    keyword: Annotated[str | None, Query(description="关键字子串匹配")] = None,
    source: Annotated[Literal["memory", "file"], Query()] = "memory",
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> LogsResponse:
    """查询运行日志：默认内存环形缓冲（最近 1 小时），可选当前文件尾部。"""
    settings = get_settings()
    if source == "file":
        entries_raw = _read_file_tail(settings.log_dir or "./logs", limit)
        if keyword:
            needle = keyword.lower()
            entries_raw = [
                entry for entry in entries_raw if needle in str(entry.get("message", "")).lower()
            ]
        return ApiResponse(
            code=0,
            message="ok",
            data=LogsResponse(
                entries=[LogEntryResponse(**entry) for entry in entries_raw],
                total=len(entries_raw),
                source="file",
            ),
        )

    now = datetime.now(UTC)
    default_start = now - timedelta(hours=1)
    start_dt = _parse_dt(start, default_start)
    end_dt = _parse_dt(end, None)
    snapshots = read_ring_buffer(
        start=start_dt,
        end=end_dt,
        levels=set(level) if level else None,
        keyword=keyword,
        limit=limit,
    )
    entries = []
    for snapshot in snapshots:
        metadata = snapshot.get("metadata") or {}
        entries.append(
            LogEntryResponse(
                timestamp=snapshot.get("timestamp"),
                level=snapshot.get("level"),
                logger=snapshot.get("logger"),
                message=snapshot.get("message"),
                request_id=snapshot.get("request_id"),
                metadata=metadata,
            )
        )
    return ApiResponse(
        code=0,
        message="ok",
        data=LogsResponse(
            entries=entries,
            total=len(entries),
            source="memory",
        ),
    )


@router.get("/ring-buffer/stats", response_model=ApiResponse[dict[str, Any]])
async def ring_buffer_stats() -> ApiResponse[dict[str, Any]]:
    """环形缓冲当前规模（前端日志页展示用）。"""
    return ApiResponse(code=0, message="ok", data={"size": ring_buffer_size()})


@router.get("/trace", response_model=ApiResponse[TraceResponse])
async def fetch_trace(
    request_id: Annotated[str | None, Query(description="请求链路 ID")] = None,
    session_id: Annotated[str | None, Query(description="对话会话 ID")] = None,
    kind: Annotated[list[str] | None, Query(description="事件类型过滤，可多值")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="最近事件条数（无链路 ID 时生效）")] = 50,
) -> ApiResponse[TraceResponse]:
    """执行链路查询。

    指定 request_id 或 session_id 时拉取该链全事件（时间升序，瀑布溯源）；
    均未指定时返回最近事件列表（时间倒序，概览，前端默认自动展示）。
    """
    if request_id or session_id:
        rows = await tool_call_log_service.fetch_trace(
            request_id=request_id,
            session_id=session_id,
            kinds=set(kind) if kind else None,
        )
        return ApiResponse(
            code=0,
            message="ok",
            data=TraceResponse(
                events=[
                    TraceEventResponse(**tool_call_log_service.serialize_event(row)) for row in rows
                ],
                total=len(rows),
                mode="trace",
            ),
        )
    rows = await tool_call_log_service.query_events(
        kinds=set(kind) if kind else None,
        limit=limit,
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=TraceResponse(
            events=[
                TraceEventResponse(**tool_call_log_service.serialize_event(row)) for row in rows
            ],
            total=len(rows),
            mode="recent",
        ),
    )


@router.post("/agent-fetch", response_model=ApiResponse[AgentLogFetchResponse])
async def fetch_agent_logs(body: AgentLogFetchRequest) -> ApiResponse[AgentLogFetchResponse]:
    """按需拉取节点本地日志（同步等待回传，超时 45s）。

    中枢下发 ``hub.log_fetch``，Agent 读本地日志文件按级别过滤 + tail 截取
    后分块回传 ``agent.log_chunk``；节点离线或旧版 Agent 未实现该指令时
    返回明确错误（409/502/504）。
    """
    try:
        content = await tool_call_log_service.request_node_logs(
            body.client_id,
            tail_lines=body.tail_lines,
            level=body.level,
        )
    except tool_call_log_service.NodeLogFetchError as exc:
        status = {"offline": 409, "send_failed": 502, "timeout": 504}.get(exc.code, 502)
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return ApiResponse(
        code=0,
        message="ok",
        data=AgentLogFetchResponse(client_id=body.client_id, content=content),
    )


def _parse_dt(raw: str | None, default: datetime | None) -> datetime | None:
    """解析 ISO 时间参数；解析失败抛 422。"""
    if raw is None:
        return default
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"时间格式非法：{raw}") from exc
