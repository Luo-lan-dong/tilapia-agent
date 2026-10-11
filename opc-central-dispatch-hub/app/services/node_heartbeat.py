"""Agent 节点心跳监控服务。

后台周期性扫描 ``agent_nodes`` 表，将超过心跳超时的在线节点标记为
``offline``，并将其负责的未完成任务重置为 ``pending``，等待调度器重新分配。
"""

import asyncio
import contextlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import contextvars

from app.core.config import get_settings
from app.models.agent_node import AgentNode, NodeStatus
from app.models.base import AsyncSessionLocal
from app.models.task import Task, TaskStatus
from app.services.dispatch_decision_service import is_ai_dispatch_enabled

logger = logging.getLogger(__name__)

# 默认值（仅测试与文档参考用）；运行期以 Pydantic Settings 为准
# （environment-config-standards §5.2：调度类阈值必须可经环境变量配置）
HEARTBEAT_INTERVAL_SECONDS = 10
HEARTBEAT_TIMEOUT_SECONDS = 30


def _bind_request_id() -> None:
    """将当前上下文的 request_id 绑定到日志上下文。"""
    request_id = contextvars.get_contextvars().get("request_id")
    if request_id:
        contextvars.bind_contextvars(request_id=request_id)


async def mark_offline_nodes(session: AsyncSession) -> list[uuid.UUID]:
    """扫描并标记超时未心跳的在线节点为 offline。

    判定条件：
    - 节点状态不是 ``offline``；
    - ``last_heartbeat_at`` 为空，或距离当前时间超过
      Settings ``heartbeat_timeout_seconds`` 秒。

    Args:
        session: 异步数据库会话。

    Returns:
        被标记为 offline 的节点 ID 列表。
    """
    timeout_threshold = datetime.now(timezone.utc) - timedelta(  # noqa: UP017
        seconds=get_settings().heartbeat_timeout_seconds,
    )

    result = await session.execute(
        select(AgentNode).where(
            AgentNode.status != NodeStatus.OFFLINE.value,
            (
                (AgentNode.last_heartbeat_at == None)  # noqa: E711
                | (AgentNode.last_heartbeat_at < timeout_threshold)
            ),
        ),
    )
    offline_nodes = result.scalars().all()
    offline_node_ids: list[uuid.UUID] = []

    for node in offline_nodes:
        node.status = NodeStatus.OFFLINE.value
        offline_node_ids.append(node.id)
        logger.warning(
            "agent_offline_detected",
            extra={
                "event": "agent_offline_detected",
                "client_id": str(node.id),
                "last_heartbeat_at": (
                    node.last_heartbeat_at.isoformat() if node.last_heartbeat_at else None
                ),
            },
        )

    if offline_node_ids:
        await session.commit()
    return offline_node_ids


async def reset_tasks_for_offline_nodes(
    session: AsyncSession,
    offline_node_ids: list[uuid.UUID],
) -> int:
    """将离线节点负责的未完成任务重新排队（ADR-059 D5 分流）。

    仅处理状态为 ``assigned`` 或 ``running`` 的任务，并清空 ``assigned_node_id``。

    **按 AI 智能调度开关分流**（用户确认的策略）：

    - **开启** → 回 ``pending``，调度器下一轮**自动改派**（先改派再告知）；
    - **关闭** → 回 ``requeue_pending``（**待确认改派**），调度器**不派发**，
      等用户确认后执行（先询问后派）。

    Args:
        session: 异步数据库会话。
        offline_node_ids: 已离线节点 ID 列表。

    Returns:
        被重新排队的任务数量。
    """
    if not offline_node_ids:
        return 0

    # ADR-059 D5：是否自动改派由 AI 智能调度开关决定
    ai_enabled = await is_ai_dispatch_enabled(session)
    target_status = TaskStatus.PENDING.value if ai_enabled else TaskStatus.REQUEUE_PENDING.value
    hold_reason = (
        "执行节点离线，任务已自动改派；已同步的产物将作为改派现场"
        if ai_enabled
        else "执行节点离线，任务已停止并等待你确认改派（确认后交调度器重新派发）"
    )

    # 先查询将被重置的任务，记录任务级超时事件。
    timeout_tasks_result = await session.execute(
        select(Task.id, Task.assigned_node_id).where(
            Task.assigned_node_id.in_(offline_node_ids),
            Task.status.in_(
                [TaskStatus.ASSIGNED.value, TaskStatus.RUNNING.value],
            ),
        ),
    )
    for task_id, node_id in timeout_tasks_result.all():
        logger.warning(
            "task_timeout",
            extra={
                "event": "task_timeout",
                "task_id": str(task_id),
                "agent_id": str(node_id),
                "timeout_seconds": get_settings().heartbeat_timeout_seconds,
                "requeued_as": target_status,
                "ai_dispatch_enabled": ai_enabled,
            },
        )

    result = await session.execute(
        update(Task)
        .where(
            Task.assigned_node_id.in_(offline_node_ids),
            Task.status.in_(
                [TaskStatus.ASSIGNED.value, TaskStatus.RUNNING.value],
            ),
        )
        .values(
            status=target_status,
            assigned_node_id=None,
            progress=0,
            # ADR-059 D3/D5：重新排队原因与「是否需人工确认」对用户可见
            dispatch_hold_reason=hold_reason,
        ),
    )
    reset_count = int(result.rowcount or 0)
    await session.commit()

    logger.info(
        "tasks_reset_for_offline_nodes",
        extra={
            "event": "tasks_reset_for_offline_nodes",
            "offline_node_count": len(offline_node_ids),
            "reset_task_count": reset_count,
            "requeued_as": target_status,
            "ai_dispatch_enabled": ai_enabled,
            "node_ids": [str(node_id) for node_id in offline_node_ids],
        },
    )
    return reset_count


async def check_heartbeat_timeout() -> list[uuid.UUID]:
    """执行一次完整的心跳超时检查。

    先标记超时节点 offline，再重置这些节点负责的未完成任务。

    Returns:
        本次被标记为 offline 的节点 ID 列表。
    """
    async with AsyncSessionLocal() as session:
        offline_node_ids = await mark_offline_nodes(session)
        if offline_node_ids:
            await reset_tasks_for_offline_nodes(session, offline_node_ids)
    return offline_node_ids


async def run_heartbeat_monitor(stop_event: asyncio.Event) -> None:
    """心跳监控后台任务主循环。

    每 Settings ``heartbeat_interval_seconds`` 秒执行一次超时检查，直到
    ``stop_event`` 被置位；每轮循环重新读取最新配置，调整阈值无需重启。

    Args:
        stop_event: 用于通知任务停止的异步事件。
    """
    settings = get_settings()
    logger.info(
        "heartbeat_monitor_started",
        extra={
            "event": "heartbeat_monitor_started",
            "interval_seconds": settings.heartbeat_interval_seconds,
            "timeout_seconds": settings.heartbeat_timeout_seconds,
        },
    )
    while not stop_event.is_set():
        try:
            await check_heartbeat_timeout()
        except Exception:
            logger.exception(
                "heartbeat_monitor_check_failed",
                extra={"event": "heartbeat_monitor_check_failed"},
            )

        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=get_settings().heartbeat_interval_seconds,
            )
        except TimeoutError:
            continue

    logger.info(
        "heartbeat_monitor_stopped",
        extra={
            "event": "heartbeat_monitor_stopped",
            "interval_seconds": get_settings().heartbeat_interval_seconds,
        },
    )


def start_heartbeat_monitor() -> tuple[asyncio.Event, asyncio.Task[None]]:
    """启动心跳监控后台任务。

    Returns:
        停止事件与后台任务对象，供 lifespan 注册使用。
    """
    stop_event = asyncio.Event()
    task = asyncio.create_task(run_heartbeat_monitor(stop_event))
    return stop_event, task


async def stop_heartbeat_monitor(
    stop_event: asyncio.Event,
    task: asyncio.Task[None],
) -> None:
    """优雅停止心跳监控后台任务。

    Args:
        stop_event: 用于通知任务停止的异步事件。
        task: 后台任务对象。
    """
    stop_event.set()
    try:
        await asyncio.wait_for(task, timeout=get_settings().heartbeat_interval_seconds)
    except TimeoutError:
        logger.warning(
            "heartbeat_monitor_stop_timeout",
            extra={
                "event": "heartbeat_monitor_stop_timeout",
                "task": task.get_name(),
            },
        )
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    except asyncio.CancelledError:
        pass
