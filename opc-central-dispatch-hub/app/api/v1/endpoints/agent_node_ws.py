"""Agent 节点 WebSocket 连接端点。

提供长连接入口供已注册 Agent 节点接入中枢，完成认证、心跳维持、
任务接收等实时通信。认证信息通过 Query String 传递：
``?node_id={uuid}&token={registration_token}``。

为避免长连接占用数据库连接，每个数据库操作均使用独立会话。
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException, status
from pydantic import ValidationError

from app.schemas.ws_messages import AgentWebSocketMessage
from app.services.agent_node_ws_service import (
    HEARTBEAT_EVENTS,
    _authenticate_node,
    _handle_heartbeat,
    _mark_node_offline,
    _mark_node_online,
)
from app.services.connection_manager import connection_manager
from app.services.task_event_handler import handle_node_event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Agent 节点通信"])


@router.websocket("/ws")
async def agent_node_websocket(websocket: WebSocket) -> None:
    """Agent 节点 WebSocket 连接入口。

    流程：
    1. 读取 ``node_id`` 与 ``token`` 并校验。
    2. 校验通过后接受连接，更新节点状态为 starting（原 offline 时）并记录连接时间。
    3. 循环接收消息，使用 ``AgentWebSocketMessage`` 校验入站消息格式。
    4. 处理 ``agent.heartbeat`` 心跳事件，根据 payload 中的 status 同步节点状态。
    5. 将任务相关事件（``agent.task_accepted`` / ``agent.task_rejected`` /
       ``agent.task_started`` / ``agent.task_progress`` / ``agent.task_completed``）
       转发给事件处理器。
    6. 未知或格式错误的事件记录日志并忽略，不影响连接。
    7. 连接断开时从连接管理器移除，并将节点置为 offline。
    """
    node_id_raw = websocket.query_params.get("node_id")
    token = websocket.query_params.get("token")
    if not node_id_raw or not token:
        logger.warning(
            "agent_ws_auth_failed",
            extra={
                "event": "agent_ws_auth_failed",
                "reason": "缺少 node_id 或 token",
                "client_host": websocket.client.host if websocket.client else None,
            },
        )
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="缺少 node_id 或 token",
        )

    try:
        agent_node = await _authenticate_node(node_id_raw, token)
    except WebSocketException:
        logger.warning(
            "agent_ws_auth_failed",
            extra={
                "event": "agent_ws_auth_failed",
                "reason": "认证失败",
                "client_host": websocket.client.host if websocket.client else None,
            },
        )
        raise
    node_id = str(agent_node.id)

    # 连接来源 IP（ADR-045 扩展）：反代场景优先 X-Forwarded-For 首段
    forwarded = websocket.headers.get("x-forwarded-for")
    client_ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (websocket.client.host if websocket.client else None)
    )

    await websocket.accept()
    await connection_manager.connect(node_id, websocket)
    was_reconnected = await _mark_node_online(agent_node, client_ip=client_ip)
    if was_reconnected:
        logger.info(
            "agent_reconnected",
            extra={
                "event": "agent_reconnected",
                "client_id": node_id,
            },
        )
    else:
        logger.info(
            "agent_ws_connected",
            extra={
                "event": "agent_ws_connected",
                "client_id": node_id,
            },
        )

    try:
        while True:
            try:
                message = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except json.JSONDecodeError:
                logger.warning(
                    "agent_ws_invalid_json",
                    extra={
                        "event": "agent_ws_invalid_json",
                        "client_id": node_id,
                    },
                )
                continue
            except Exception:
                # 2026-10-02：连接级错误（如 Starlette "not connected" RuntimeError）
                # 此前被 continue 吞掉导致热循环刷 ERROR 日志（单个日志文件 5 分钟
                # 涨到 400MB 并把事件循环打满）；此处直接退出，由节点重连退避恢复。
                logger.exception(
                    "agent_ws_receive_error",
                    extra={
                        "event": "agent_ws_receive_error",
                        "client_id": node_id,
                    },
                )
                break

            try:
                ws_message = AgentWebSocketMessage.model_validate(message)
            except ValidationError as exc:
                logger.warning(
                    "agent_ws_invalid_message",
                    extra={
                        "event": "agent_ws_invalid_message",
                        "client_id": node_id,
                        "error": str(exc),
                    },
                    exc_info=True,
                )
                continue

            if ws_message.event in HEARTBEAT_EVENTS:
                await _handle_heartbeat(node_id, ws_message.payload, client_ip=client_ip)
            else:
                await handle_node_event(node_id, ws_message.event, ws_message.payload)
    except WebSocketDisconnect:
        logger.info(
            "agent_ws_disconnected",
            extra={
                "event": "agent_ws_disconnected",
                "client_id": node_id,
            },
        )
    except Exception:
        logger.exception(
            "agent_ws_error",
            extra={
                "event": "agent_ws_error",
                "client_id": node_id,
            },
        )
    finally:
        try:
            is_current = await connection_manager.disconnect_if_current(
                node_id,
                websocket,
            )
            if is_current:
                # 使用 shield 保护离线落库，避免连接取消导致状态未更新
                await asyncio.shield(_mark_node_offline(node_id))
        except asyncio.CancelledError:
            logger.warning(
                "agent_ws_cleanup_cancelled",
                extra={
                    "event": "agent_ws_cleanup_cancelled",
                    "client_id": node_id,
                },
            )
