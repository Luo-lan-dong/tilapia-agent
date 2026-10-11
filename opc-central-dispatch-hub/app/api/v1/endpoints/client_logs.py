"""前端错误日志上报端点。

接收 web 控制台 errorReporter 上报的未捕获异常（window.onerror /
unhandledrejection），写入中枢运行日志（WARNING 级，生产环境 WARN+
自动归档 error.log），便于把前端崩溃与后端日志统一排查。
"""

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("web.console")

router = APIRouter(tags=["客户端日志"])


class ClientLogRequest(BaseModel):
    """前端错误日志上报请求体（errorReporter.ts ErrorReport）。"""

    message: str = Field(..., min_length=1, max_length=2000, description="错误消息")
    stack: str | None = Field(None, max_length=8000, description="错误堆栈")
    filename: str | None = Field(None, max_length=500, description="触发错误的脚本文件")
    lineno: int | None = Field(None, description="行号")
    colno: int | None = Field(None, description="列号")
    url: str | None = Field(None, max_length=1000, description="发生页面 URL")
    timestamp: str | None = Field(None, max_length=64, description="上报时间 ISO 8601")


@router.post(
    "",
    status_code=204,
    summary="前端错误日志上报",
    description=(
        "接收 web 控制台未捕获异常并写入中枢运行日志；上报失败由前端静默处理，"
        "本端点恒不抛业务错误（204），避免错误上报本身产生错误循环。"
    ),
)
async def receive_client_log(payload: ClientLogRequest, request: Request) -> None:
    """记录前端异常（WARNING 级，堆栈截断 4KB）。"""
    location = f"{payload.filename or 'unknown'}:{payload.lineno or 0}:{payload.colno or 0}"
    logger.warning(
        "web_console_error",
        extra={
            "event": "web_console_error",
            "error_message": payload.message[:500],
            "location": location,
            "page_url": (payload.url or "")[:300],
            "reported_at": payload.timestamp,
            "stack": (payload.stack or "")[:4000],
            "client_ip": request.client.host if request.client else None,
        },
    )
