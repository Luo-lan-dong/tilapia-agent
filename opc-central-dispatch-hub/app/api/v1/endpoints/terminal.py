"""终端 API（ADR-053）：PTY WebSocket 通道 + 权限模式 + 审批决定。"""

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from app.api.deps import get_async_session
from app.core.errors import BusinessError, ErrorCode
from app.schemas.common import ApiResponse
from app.schemas.terminal import (
    ApprovalDecisionRequest,
    PermissionModeResponse,
    PermissionModeUpdate,
)
from app.services.system_settings_service import SystemSettingsService
from app.services.terminal_approval import terminal_approval_registry
from app.services.terminal_service import terminal_service

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_MODES = ("manual", "ai_auto", "full")
_settings_service = SystemSettingsService()


async def _read_mode(session) -> str:
    """读取当前权限模式（NULL 回退 manual）。"""
    settings = await _settings_service.get_network_settings(session)
    mode = settings.terminal_permission_mode
    return mode if mode in _VALID_MODES else "manual"


@router.get("/permission-mode", response_model=ApiResponse[PermissionModeResponse])
async def get_permission_mode(session=Depends(get_async_session)) -> ApiResponse[PermissionModeResponse]:
    """获取终端命令执行权限模式（ADR-053）。"""
    return ApiResponse(code=0, message="ok", data=PermissionModeResponse(mode=await _read_mode(session)))


@router.put("/permission-mode", response_model=ApiResponse[PermissionModeResponse])
async def update_permission_mode(
    payload: PermissionModeUpdate, session=Depends(get_async_session)
) -> ApiResponse[PermissionModeResponse]:
    """切换终端命令执行权限模式（热生效）。"""
    if payload.mode not in _VALID_MODES:
        raise BusinessError(
            status_code=422,
            biz_code=ErrorCode.TERMINAL_MODE_INVALID,
            detail=f"非法权限模式：{payload.mode}（可选 manual/ai_auto/full）",
        )
    settings = await _settings_service.get_network_settings(session)
    settings.terminal_permission_mode = payload.mode
    await session.commit()
    logger.info(
        "terminal_permission_mode_updated",
        extra={"event": "terminal_permission_mode_updated", "mode": payload.mode},
    )
    return ApiResponse(code=0, message="ok", data=PermissionModeResponse(mode=payload.mode))


@router.post("/approvals/{approval_id}", response_model=ApiResponse[None])
async def decide_approval(approval_id: str, payload: ApprovalDecisionRequest) -> ApiResponse[None]:
    """用户批准/拒绝终端命令（置位审批事件，唤醒等待中的技能）。"""
    approval = terminal_approval_registry.resolve(approval_id, payload.approve)
    if approval is None:
        raise BusinessError(
            status_code=404,
            biz_code=ErrorCode.TERMINAL_NOT_FOUND,
            detail=f"审批请求不存在或已处理：{approval_id}",
        )
    logger.info(
        "terminal_approval_decided",
        extra={
            "event": "terminal_approval_decided",
            "approval_id": approval_id,
            "approved": payload.approve,
        },
    )
    return ApiResponse(code=0, message="ok", data=None)


@router.websocket("/ws")
async def terminal_ws(
    websocket: WebSocket,
    session_id: str = Query(..., description="对话会话 ID（PTY 会话键）"),
    work_dir: str = Query(default="", description="会话工作目录（首次创建 PTY 时生效）"),
) -> None:
    """终端双向通道：上行 input/resize，下行 output/ai_echo（广播）。"""
    await websocket.accept()
    try:
        session = await terminal_service.get_or_create(
            session_id, work_dir or terminal_service.default_cwd()
        )
    except BusinessError as exc:
        await websocket.send_json({"type": "error", "detail": exc.detail})
        await websocket.close()
        return

    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=512)
    session.subscribers.add(queue)
    # 新连接回放最近输出（环形缓冲）
    replay = session.replay()
    if replay:
        await websocket.send_json({"type": "output", "data": replay})

    async def _pump_out() -> None:
        while True:
            data = await queue.get()
            await websocket.send_json({"type": "output", "data": data})

    async def _pump_in() -> None:
        while True:
            text = await websocket.receive_text()
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            msg_type = msg.get("type")
            if msg_type == "input":
                terminal_service.write_input(session_id, str(msg.get("data") or ""))
            elif msg_type == "resize":
                cols = int(msg.get("cols") or 120)
                rows = int(msg.get("rows") or 30)
                terminal_service.resize(session_id, cols, rows)

    try:
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(_pump_out()),
                asyncio.create_task(_pump_in()),
            ],
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        session.subscribers.discard(queue)
        with contextlib.suppress(Exception):
            await websocket.close()
