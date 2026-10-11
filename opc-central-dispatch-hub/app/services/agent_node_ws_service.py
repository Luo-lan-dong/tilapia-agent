"""Agent 节点 WebSocket 连接辅助服务。

提供 ``agent_node_ws`` 端点所需的认证、状态更新、心跳处理等辅助逻辑，
将 WebSocket 入口与业务处理解耦。
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocketException, status
from pydantic import ValidationError
from sqlalchemy import select
from structlog import contextvars

from app.models.agent_node import AgentNode, NodeStatus
from app.models.base import AsyncSessionLocal
from app.models.task import Task, TaskStatus
from app.schemas.ws_messages import (
    AGENT_HEARTBEAT,
    HUB_TASK_CANCEL,
    AgentWebSocketMessage,
)
from app.services.connection_manager import connection_manager

logger = logging.getLogger(__name__)

HEARTBEAT_EVENTS: set[str] = {AGENT_HEARTBEAT}


def _bind_request_id() -> None:
    """将当前上下文的 request_id 绑定到日志上下文。"""
    request_id = contextvars.get_contextvars().get("request_id")
    if request_id:
        contextvars.bind_contextvars(request_id=request_id)


def _parse_event(message: Any) -> tuple[str | None, dict[str, Any]]:
    """解析 WebSocket 消息，提取事件名与 payload。

    建议使用 ``AgentWebSocketMessage.model_validate`` 替代本函数进行入站校验。

    Args:
        message: WebSocket 收到的 JSON 消息。

    Returns:
        事件名字符串与 payload 字典；格式错误时返回 ``(None, {})``。
    """
    try:
        ws_message = AgentWebSocketMessage.model_validate(message)
    except ValidationError as exc:
        logger.warning(
            "agent_ws_invalid_message_format",
            extra={
                "event": "agent_ws_invalid_message_format",
                "message": str(message)[:200],
                "error": str(exc),
            },
            exc_info=True,
        )
        return None, {}

    return ws_message.event, ws_message.payload


async def _authenticate_node(node_id_raw: str, token: str) -> AgentNode:
    """校验节点凭据并返回节点记录。

    Args:
        node_id_raw: Query String 中的节点 ID 字符串。
        token: Query String 中的注册令牌。

    Returns:
        校验通过的 AgentNode 实例。

    Raises:
        WebSocketException: 凭据无效时抛出，WebSocket 连接将被拒绝。
    """
    try:
        node_id = uuid.UUID(node_id_raw)
    except ValueError as exc:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="无效的 node_id 格式",
        ) from exc

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgentNode).where(
                AgentNode.id == node_id,
                AgentNode.registration_token == token,
            ),
        )
        agent_node = result.scalar_one_or_none()
        if agent_node is None:
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="node_id 或 token 无效",
            )
        return agent_node


async def _mark_node_online(agent_node: AgentNode, client_ip: str | None = None) -> bool:
    """标记节点为在线状态。

    节点认证通过后调用：
    - 若节点原状态为 offline，则置为 starting（等待 Agent 上报 idle 后才可调度）。
    - 更新 ``last_heartbeat_at`` 为当前时间，作为连接时间记录。
    - 记录连接来源 IP（reported_ip，ADR-045 扩展：中枢从 WebSocket 连接
      直接提取，节点管理/仪表盘展示；ADR-035 自报地址缺省时的兜底来源）。

    Args:
        agent_node: 待更新的 AgentNode 实例。
        client_ip: WebSocket 连接的客户端 IP（反代场景取 X-Forwarded-For）。

    Returns:
        节点是否从 offline 恢复为 online。
    """
    async with AsyncSessionLocal() as session:
        fresh_node = await session.get(AgentNode, agent_node.id)
        if fresh_node is None:
            return False
        now = datetime.now(UTC)
        was_offline = fresh_node.status == NodeStatus.OFFLINE.value
        if was_offline:
            fresh_node.status = NodeStatus.STARTING.value
        fresh_node.last_heartbeat_at = now
        if client_ip:
            fresh_node.reported_ip = client_ip
        await session.commit()
        # 节点上线/重连日志由端点层按 was_offline 分支记录
        # （agent_ws_connected / agent_reconnected），此处不再重复打点
        return was_offline


async def _mark_node_offline(node_id: str) -> None:
    """标记节点为离线状态。

    WebSocket 连接断开时调用，将节点状态重置为 offline。

    Args:
        node_id: Agent 节点 ID 字符串。
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgentNode).where(AgentNode.id == uuid.UUID(node_id)),
        )
        agent_node = result.scalar_one_or_none()
        if agent_node is None:
            return
        agent_node.status = NodeStatus.OFFLINE.value
        await session.commit()
        logger.info(
            "agent_offline_detected",
            extra={
                "event": "agent_offline_detected",
                "client_id": node_id,
            },
        )


async def _handle_heartbeat(
    node_id: str,
    payload: dict[str, Any],
    client_ip: str | None = None,
) -> None:
    """处理节点心跳事件。

    更新 ``last_heartbeat_at`` 时间戳，并根据 payload 中的 ``status`` 字段
    同步节点状态（ADR-032：``busy`` 为纯展示态，由 Agent 心跳上报派生——
    有在跑任务上报 busy、无在跑任务上报 idle，任务终态后自动恢复空闲）。若
    payload 中包含 ``metrics`` 监控快照，则持久化到 ``metrics_snapshot``
    并将 ``load`` 字段更新为综合负载得分。心跳顺带刷新连接来源 IP
    （reported_ip，节点 IP 变化时自动保鲜）。

    Args:
        node_id: 发送心跳的 Agent 节点 ID 字符串。
        payload: 心跳事件负载，可能包含 ``status`` / ``load`` / ``metrics`` 字段。
        client_ip: WebSocket 连接的客户端 IP。
    """
    reported_status = payload.get("status")
    metrics = payload.get("metrics")
    reported_task_id = payload.get("current_task_id")
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgentNode).where(AgentNode.id == uuid.UUID(node_id)),
        )
        agent_node = result.scalar_one_or_none()
        if agent_node is None:
            return
        agent_node.last_heartbeat_at = datetime.now(UTC)
        # IP 优先级（2026-09-24）：Agent 心跳自报的本机出口 IP（最真实，
        # VPN/NAT 下 WS 连接源会失真为网关地址）> WS 连接源地址
        reported_ip_from_agent = payload.get("ip")
        effective_ip = (
            reported_ip_from_agent
            if isinstance(reported_ip_from_agent, str) and reported_ip_from_agent
            else client_ip
        )
        if effective_ip:
            agent_node.reported_ip = effective_ip
        # 环境签名（ADR-061 M6，Q14）：节点心跳上报的 OS/架构/运行时/工具版本短哈希，
        # 供经验记忆跨机召回按环境匹配；旧节点不报则保持原值（NULL）
        env_signature = payload.get("environment_signature")
        if isinstance(env_signature, str) and env_signature.strip():
            agent_node.environment_signature = env_signature.strip()[:64]
        # ADR-032：busy 为纯展示态，保留心跳上报值（starting/idle/busy 均同步），
        # 任务失败/完成后 Agent 心跳上报 idle 即自动恢复空闲，不再被 sticky busy 拦截
        if reported_status in (
            NodeStatus.STARTING.value,
            NodeStatus.IDLE.value,
            NodeStatus.BUSY.value,
        ):
            agent_node.status = reported_status

        load_value = _resolve_load_value(metrics, payload.get("load"))
        if load_value is not None:
            agent_node.load = load_value

        if isinstance(metrics, dict):
            agent_node.metrics_snapshot = metrics

        await session.commit()
        logger.debug(
            "agent_heartbeat_received",
            extra={
                "event": "agent_heartbeat_received",
                "client_id": node_id,
                "status": agent_node.status,
                "load": agent_node.load,
                "has_metrics": isinstance(metrics, dict),
            },
        )

    # 重连任务对账（ADR-020）：节点断线超过心跳超时后，其执行中任务会被心跳
    # 监控重置为 pending 并可能改派其他节点；节点恢复连接后的心跳仍携带
    # current_task_id，需校验归属并按需恢复归属或通知节点本地取消
    if isinstance(reported_task_id, str) and reported_task_id:
        await _reconcile_reported_task(node_id, reported_task_id)


async def _reconcile_reported_task(node_id: str, reported_task_id: str) -> None:
    """对账节点心跳上报的执行中任务归属（断线重连场景，见 ADR-020）。

    按以下规则对账：
    - 任务正常归属该节点 → 无需处理；
    - 任务为 ``pending`` 且未改派（断线期间被心跳监控重置）→ 恢复归属给该
      节点（置 running），保留断线期间的工作成果，避免任务重复执行；
    - 任务已改派他人 / 已终态 / 不存在 → 推送 ``hub.task_cancel`` 通知节点
      本地中断执行。

    Args:
        node_id: 上报心跳的节点 ID 字符串。
        reported_task_id: 节点心跳携带的执行中任务 ID 字符串。
    """
    try:
        task_uuid = uuid.UUID(reported_task_id)
        node_uuid = uuid.UUID(node_id)
    except ValueError:
        return

    cancel_reason: str | None = None
    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_uuid)
        if task is None:
            cancel_reason = "任务不存在"
        elif task.assigned_node_id == node_uuid:
            # 正常归属，无需对账
            return
        elif task.status == TaskStatus.PENDING.value and task.assigned_node_id is None:
            # 断线期间被重置且尚未改派：恢复归属，保留节点侧工作成果
            task.assigned_node_id = node_uuid
            task.status = TaskStatus.RUNNING.value
            await session.commit()
            logger.info(
                "task_reclaimed_after_reconnect",
                extra={
                    "event": "task_reclaimed_after_reconnect",
                    "task_id": reported_task_id,
                    "client_id": node_id,
                },
            )
            return
        else:
            cancel_reason = f"任务已不再归属本节点（当前状态 {task.status}）"

    sent = await connection_manager.send_to_node(
        node_id,
        {
            "event": HUB_TASK_CANCEL,
            "payload": {"task_id": reported_task_id, "reason": cancel_reason},
            "timestamp": datetime.now(UTC).isoformat(),
            "request_id": None,
        },
    )
    logger.warning(
        "task_cancel_pushed_after_reconnect",
        extra={
            "event": "task_cancel_pushed_after_reconnect",
            "task_id": reported_task_id,
            "client_id": node_id,
            "reason": cancel_reason,
            "delivered": sent,
        },
    )


def _resolve_load_value(metrics: Any, fallback_load: Any) -> float | None:
    """从 metrics 快照或 fallback 中解析综合负载得分。

    优先级：``metrics.load_score.score`` > ``fallback_load`` > ``None``。
    返回 ``None`` 表示不更新 ``agent_nodes.load`` 字段。

    Args:
        metrics: 监控快照字典，可能为 None 或无效类型。
        fallback_load: payload 中的 load 字段，作为旧版 Agent 兼容回退。

    Returns:
        解析后的负载浮点值，或 None。
    """
    if isinstance(metrics, dict):
        load_score = metrics.get("load_score")
        if isinstance(load_score, dict):
            score = load_score.get("score")
            if isinstance(score, (int, float)):
                return float(score)

    if isinstance(fallback_load, (int, float)):
        return float(fallback_load)
    return None
