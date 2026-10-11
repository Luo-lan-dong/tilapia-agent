"""Agent 节点任务事件处理器。

将 WebSocket 收到的事件（agent.task_accepted / agent.task_rejected /
agent.task_started / agent.task_progress / agent.task_completed /
agent.task_failed / agent.node_chat_reply / agent.node_chat_error）
转换为数据库状态变更或会话消息落库。
所有数据库操作均使用独立异步会话完成，避免长连接占用连接池。

注：本文件超过 300 行上限（见 docs/standards/coding-standards.md 4.3）。
六个事件处理器结构同构（校验 → 状态机检查 → 落库）且经统一分发表注册，
拆分为多文件会割裂同构逻辑并增加导入碎片，故按豁免场景保留并在此说明。

安全设计：
- 每次处理都校验 task_id 对应的任务确实分配给了当前节点，防止伪造。
- 异常均在内部捕获并记录，不会导致 WebSocket 连接断开。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_node import AgentNode
from app.models.base import AsyncSessionLocal
from app.models.task import Task, TaskStatus
from app.models.task_building_session import TaskBuildingSession
from app.models.tool_call_log import ToolCallLogKind
from app.schemas.ws_messages import (
    AGENT_FILE_CHUNK,
    AGENT_FILE_ERROR,
    AGENT_FILE_TREE,
    AGENT_LOG_CHUNK,
    AGENT_LOG_ERROR,
    AGENT_NODE_CHAT_ERROR,
    AGENT_NODE_CHAT_REPLY,
    AGENT_TASK_ACCEPTED,
    AGENT_TASK_COMPLETED,
    AGENT_TASK_FAILED,
    AGENT_TASK_PROGRESS,
    AGENT_TASK_REJECTED,
    AGENT_TASK_STARTED,
    AGENT_TASK_VERIFY_BLOCKED,
    TASK_PROGRESS_MESSAGE_MAX_CHARS,
    TASK_PROGRESS_STAGES,
)
from app.services import dispatch_backoff, tool_call_log_service
from app.services.artifact_service import artifact_service
from app.services.task_building_session_service import TaskBuildingSessionService
from app.services.task_handoff_service import chain_root, upsert_chain_context

logger = logging.getLogger(__name__)

#: 节点上报长文本（plan_content / change_summary / project_context）超长阈值
#: （字符数），超出部分截断存储，避免异常上报撑爆数据库行
REPORTED_TEXT_MAX_LENGTH = 200_000

#: 构建会话保留的最大消息条数（ADR-061 M4 主动通知时裁剪，避免无限增长）
_DECISION_MESSAGE_KEEP = 500

#: 节点上报当前工具名的长度上限（ADR-061 M5a，与 `AgentTaskProgressPayload.tool` 一致）
TASK_PROGRESS_TOOL_MAX_CHARS = 64

#: 增量产物同步在跑的任务集合（ADR-059 D4）：同任务同时只跑一轮
_incremental_sync_inflight: set[str] = set()


def _schedule_artifact_sync(task_id: str, node_id: str) -> None:
    """任务完成后后台全量同步产物到中枢（ADR-034 现场迁移的数据来源）。

    Agent 尚在线时拉取整目录 zip 落盘（export.zip）；失败仅告警——
    产物同步失败不影响任务完成状态，也不阻断事件处理。
    """

    async def _sync() -> None:
        try:
            await artifact_service.fetch_zip(task_id, node_id)
            logger.info(
                "artifact_full_sync_completed",
                extra={"event": "artifact_full_sync_completed", "task_id": task_id},
            )
        except Exception as exc:
            logger.warning(
                "artifact_full_sync_failed",
                extra={
                    "event": "artifact_full_sync_failed",
                    "task_id": task_id,
                    "reason": str(exc),
                },
            )

    asyncio.create_task(_sync(), name=f"artifact-sync-{task_id}")


def _schedule_incremental_sync(task_id: str, node_id: str, files: list[dict]) -> None:
    """对最新文件清单做一轮后台增量同步（ADR-059 D4）。

    触发点复用节点周期上报的 ``agent.file_tree``（节点侧每 30 秒一次），
    因此无需中枢另起定时器：清单到达即 diff + 拉取变更文件。
    同一任务同时只允许一轮在跑（``_incremental_sync_inflight``），
    避免慢节点上任务堆积导致并发拉取风暴。
    """

    async def _sync() -> None:
        try:
            synced = await artifact_service.sync_incremental(task_id, node_id, files)
            if synced:
                logger.info(
                    "artifact_incremental_synced",
                    extra={
                        "event": "artifact_incremental_synced",
                        "task_id": task_id,
                        "synced": synced,
                    },
                )
        except Exception as exc:  # noqa: BLE001 — 增量同步失败不影响任务
            logger.warning(
                "artifact_incremental_sync_failed",
                extra={
                    "event": "artifact_incremental_sync_failed",
                    "task_id": task_id,
                    "reason": str(exc),
                },
            )
        finally:
            _incremental_sync_inflight.discard(task_id)

    if task_id in _incremental_sync_inflight:
        return
    _incremental_sync_inflight.add(task_id)
    asyncio.create_task(_sync(), name=f"artifact-incr-{task_id}")


async def _get_verified_task(
    session: AsyncSession,
    task_id_raw: str,
    node_id_raw: str,
) -> Task | None:
    """查询任务并校验是否确实分配给指定节点。

    Args:
        session: 异步数据库会话。
        task_id_raw: 任务 ID 字符串。
        node_id_raw: 节点 ID 字符串。

    Returns:
        校验通过的任务；ID 格式错误、任务不存在或分配关系不匹配时返回 ``None``。
    """
    try:
        task_id = uuid.UUID(task_id_raw)
        node_id = uuid.UUID(node_id_raw)
    except ValueError:
        logger.warning(
            "task_event_invalid_id",
            extra={
                "event": "task_event_invalid_id",
                "task_id": task_id_raw,
                "client_id": node_id_raw,
            },
        )
        return None

    task = await session.get(Task, task_id)
    if task is None:
        logger.warning(
            "task_event_task_not_found",
            extra={
                "event": "task_event_task_not_found",
                "task_id": task_id_raw,
                "client_id": node_id_raw,
            },
        )
        return None

    if task.assigned_node_id != node_id:
        logger.warning(
            "task_event_not_assigned_to_node",
            extra={
                "event": "task_event_not_assigned_to_node",
                "task_id": task_id_raw,
                "client_id": node_id_raw,
                "assigned_node_id": str(task.assigned_node_id),
            },
        )
        return None

    return task


async def _get_node(session: AsyncSession, node_id_raw: str) -> AgentNode | None:
    """根据字符串 ID 查询节点记录。

    Args:
        session: 异步数据库会话。
        node_id_raw: 节点 ID 字符串。

    Returns:
        节点记录；ID 格式错误或不存在时返回 ``None``。
    """
    try:
        node_id = uuid.UUID(node_id_raw)
    except ValueError:
        return None
    return await session.get(AgentNode, node_id)


async def _node_display_name(session: AsyncSession, node_id_raw: str) -> str:
    """取节点展示名（ADR-059 D3）；查不到时回退为截断的节点 ID。"""
    node = await _get_node(session, node_id_raw)
    if node is not None and node.name:
        return node.name
    return str(node_id_raw)[:8]


async def _handle_task_accepted(node_id: str, payload: dict[str, Any]) -> None:
    """处理节点接受任务事件：任务状态变为 running。"""
    task_id = payload.get("task_id")
    if not task_id:
        logger.warning(
            "task_accepted_missing_task_id",
            extra={"event": "task_accepted_missing_task_id", "client_id": node_id},
        )
        return

    async with AsyncSessionLocal() as session:
        task = await _get_verified_task(session, task_id, node_id)
        if task is None:
            return
        if task.status != TaskStatus.ASSIGNED.value:
            logger.warning(
                "task_accepted_invalid_status",
                extra={
                    "event": "task_accepted_invalid_status",
                    "task_id": task_id,
                    "client_id": node_id,
                    "status": task.status,
                },
            )
            return

        task.status = TaskStatus.RUNNING.value
        task.last_reject_reason = None
        task.dispatch_hold_reason = None
        await session.commit()
        await tool_call_log_service.record(
            kind="agent_task_event",
            name="task_accepted",
            task_id=task_id,
            result_summary={"agent_id": node_id},
        )
        logger.info(
            "task_accepted",
            extra={
                "event": "task_accepted",
                "task_id": task_id,
                "agent_id": node_id,
            },
        )


async def _handle_task_rejected(node_id: str, payload: dict[str, Any]) -> None:
    """处理节点拒绝任务事件：任务回 pending 并记录回避（ADR-032）。

    节点状态不再由本处理器改写（心跳派生）；拒绝节点记入回避集合
    （5 分钟 TTL），下一轮调度自动改派其他候选节点，全部不可用时
    任务保持 pending 等待。
    """
    task_id = payload.get("task_id")
    if not task_id:
        logger.warning(
            "task_rejected_missing_task_id",
            extra={"event": "task_rejected_missing_task_id", "client_id": node_id},
        )
        return

    async with AsyncSessionLocal() as session:
        task = await _get_verified_task(session, task_id, node_id)
        if task is None:
            return
        if task.status != TaskStatus.ASSIGNED.value:
            logger.warning(
                "task_rejected_invalid_status",
                extra={
                    "event": "task_rejected_invalid_status",
                    "task_id": task_id,
                    "client_id": node_id,
                    "status": task.status,
                },
            )
            return

        # 旧 Agent 无 reason 字段时按 slots_full 处理（协议向后兼容）
        reason = payload.get("reason") or "slots_full"
        dispatch_backoff.record_rejection(task_id, node_id)

        # ADR-059 D3：拒单原因落库对用户可见（不再只有一句「排队中 0%」）
        node_name = await _node_display_name(session, node_id)
        task.status = TaskStatus.PENDING.value
        task.assigned_node_id = None
        task.progress = 0
        task.last_reject_reason = str(reason)[:64]
        task.dispatch_hold_reason = f"节点「{node_name}」以 {reason} 拒单，已回避 5 分钟"[:200]
        await session.commit()
        logger.info(
            "task_rejected",
            extra={
                "event": "task_rejected",
                "task_id": task_id,
                "agent_id": node_id,
                "reject_reason": reason,
            },
        )


async def _handle_task_started(node_id: str, payload: dict[str, Any]) -> None:
    """处理节点开始执行任务事件：任务状态变为 running。"""
    task_id = payload.get("task_id")
    if not task_id:
        logger.warning(
            "task_started_missing_task_id",
            extra={"event": "task_started_missing_task_id", "client_id": node_id},
        )
        return

    async with AsyncSessionLocal() as session:
        task = await _get_verified_task(session, task_id, node_id)
        if task is None:
            return
        if task.status not in {TaskStatus.ASSIGNED.value, TaskStatus.RUNNING.value}:
            logger.warning(
                "task_started_invalid_status",
                extra={
                    "event": "task_started_invalid_status",
                    "task_id": task_id,
                    "client_id": node_id,
                    "status": task.status,
                },
            )
            return

        task.status = TaskStatus.RUNNING.value
        # 实际执行模型（ADR-029 增强）：节点在 session 创建后上报，按列长截断
        actual_model = payload.get("executor_model_actual")
        if isinstance(actual_model, str) and actual_model.strip():
            task.executor_model_actual = actual_model.strip()[:200]
        await session.commit()
        await tool_call_log_service.record(
            kind="agent_task_event",
            name="task_started",
            task_id=task_id,
            result_summary={
                "agent_id": node_id,
                "executor_model_actual": task.executor_model_actual,
            },
        )
        logger.info(
            "task_started",
            extra={
                "event": "task_started",
                "task_id": task_id,
                "agent_id": node_id,
                "executor_model_actual": task.executor_model_actual,
            },
        )


async def _handle_task_progress(node_id: str, payload: dict[str, Any]) -> None:
    """处理节点上报进度事件：更新任务 progress 与当前动作（ADR-055）。

    新增字段（``stage``/``message``/``tool``/``detail``）为**可选且宽松**校验：
    非封闭枚举的 stage、超长 message 只记录告警并忽略该字段，不拒绝整条事件
    —— 进度本身仍要落库，避免个别字段异常导致前端完全看不到进度。
    """
    task_id = payload.get("task_id")
    progress = payload.get("progress")

    if not task_id:
        logger.warning(
            "task_progress_missing_task_id",
            extra={"event": "task_progress_missing_task_id", "client_id": node_id},
        )
        return
    if not isinstance(progress, int) or not 0 <= progress <= 100:
        logger.warning(
            "task_progress_invalid_value",
            extra={
                "event": "task_progress_invalid_value",
                "task_id": task_id,
                "client_id": node_id,
                "progress": progress,
            },
        )
        return

    stage = _sanitize_progress_stage(payload.get("stage"), task_id, node_id)
    message = _sanitize_progress_message(payload.get("message"), task_id, node_id)
    # ADR-061 M5a（UT-M5-4）：tool 字段此前上报即被丢弃
    tool = _sanitize_progress_tool(payload.get("tool"), task_id, node_id)

    async with AsyncSessionLocal() as session:
        task = await _get_verified_task(session, task_id, node_id)
        if task is None:
            return

        # 动作历史（ADR-061 M5c，Q12）：工具或阶段**发生变化**时留痕，
        # 供只读时间线离线回看「做过什么」（同一动作不重复记，天然节流）
        previous_stage = task.progress_stage
        previous_tool = task.progress_tool
        task.progress = progress
        if stage is not None:
            task.progress_stage = stage
        if message is not None:
            task.progress_message = message
        if tool is not None:
            task.progress_tool = tool
        activity_changed = tool is not None and tool != previous_tool
        stage_changed = stage is not None and stage != previous_stage
        todos = payload.get("todos")
        if todos is not None:
            if isinstance(todos, list) and all(isinstance(item, dict) for item in todos):
                task.todos = todos
            else:
                logger.warning(
                    "task_progress_invalid_todos",
                    extra={
                        "event": "task_progress_invalid_todos",
                        "task_id": task_id,
                        "client_id": node_id,
                        "todos_type": type(todos).__name__,
                    },
                )
        await session.commit()
        logger.info(
            "task_progress_updated",
            extra={
                "event": "task_progress_updated",
                "task_id": task_id,
                "agent_id": node_id,
                "progress_percent": progress,
                # ADR-055：记录节点上报的**真实**阶段（此前硬编码 execution）
                "stage": stage or "execution",
            },
        )

    # LLM 前缀缓存指标（ADR-060 P0）：节点已在 detail.tokens 中携带 cache read/write，
    # 此前中枢完全忽略该字段，导致节点侧缓存命中率不可见。
    # ADR-061 M5a：同一次解析结果同时落库（kind=llm_usage），供用量曲线与预算闸查询。
    await _record_cache_metrics(task_id, node_id, payload.get("detail"), tool)

    # ADR-061 M5c：动作留痕（工具/阶段变化时一条），只读时间线的数据源
    if activity_changed or stage_changed:
        await tool_call_log_service.record(
            kind=ToolCallLogKind.AGENT_TASK_EVENT.value,
            name="tool_activity",
            task_id=str(task_id),
            result_summary={
                "stage": stage or previous_stage,
                "tool": tool or previous_tool,
                "message": message,
                "progress": progress,
            },
        )


def _parse_usage_metrics(detail: Any) -> dict[str, Any] | None:
    """解析节点上报的 LLM 用量与缓存指标（ADR-060 P0 口径，ADR-061 M5a 复用）。

    返回归一后的整数字典；``detail``/``tokens`` 结构异常、或 input 与
    cache_read 皆为 0（首轮无缓存的空样本）时返回 ``None``，既不记日志
    也不落库，避免指标噪音。
    """
    if not isinstance(detail, dict):
        return None
    tokens = detail.get("tokens")
    if not isinstance(tokens, dict):
        return None

    def _as_int(value: Any) -> int:
        return int(value) if isinstance(value, (int, float)) and value >= 0 else 0

    input_tokens = _as_int(tokens.get("input"))
    cache_read = _as_int(tokens.get("cache_read"))
    cache_write = _as_int(tokens.get("cache_write"))
    if input_tokens == 0 and cache_read == 0:
        return None
    denominator = input_tokens + cache_read
    cost = detail.get("cost")
    files = detail.get("files")
    return {
        "input_tokens": input_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "output_tokens": _as_int(tokens.get("output")),
        "reasoning_tokens": _as_int(tokens.get("reasoning")),
        "cache_hit_rate": round(cache_read / denominator, 4) if denominator else 0.0,
        "cost": float(cost) if isinstance(cost, (int, float)) else None,
        "files_touched": len(files) if isinstance(files, list) else 0,
    }


async def _record_cache_metrics(
    task_id: str,
    node_id: str,
    detail: Any,
    tool: str | None = None,
) -> None:
    """记录**并落库**节点侧 LLM 用量与缓存命中率（ADR-060 P0 + ADR-061 M5a）。

    口径与 tilacode 会话账本一致：``hit_rate = cache_read / (input + cache_read)``。
    落库复用 ``tool_call_log_service.record``（独立会话 + 内部兜底），指标写失败
    只告警，绝不影响进度主流程。字段缺失或非法时静默跳过。
    """
    metrics = _parse_usage_metrics(detail)
    if metrics is None:
        return
    logger.info(
        "task_progress_cache_metrics",
        extra={
            "event": "task_progress_cache_metrics",
            "task_id": task_id,
            "agent_id": node_id,
            **metrics,
        },
    )
    await tool_call_log_service.record(
        kind=ToolCallLogKind.LLM_USAGE.value,
        name="agent_llm_usage",
        task_id=task_id,
        args_summary={"agent_id": node_id, "tool": tool},
        result_summary=metrics,
    )


def _sanitize_progress_stage(raw: Any, task_id: str, node_id: str) -> str | None:
    """校验并规整进度阶段；非法值告警并返回 None（不落库）。"""
    if raw is None:
        return None
    if isinstance(raw, str) and raw in TASK_PROGRESS_STAGES:
        return raw
    logger.warning(
        "task_progress_unknown_stage",
        extra={
            "event": "task_progress_unknown_stage",
            "task_id": task_id,
            "client_id": node_id,
            "stage": str(raw)[:64],
        },
    )
    return None


def _sanitize_progress_message(raw: Any, task_id: str, node_id: str) -> str | None:
    """校验并截断当前动作描述；非法类型告警并返回 None（不落库）。"""
    if raw is None:
        return None
    if not isinstance(raw, str):
        logger.warning(
            "task_progress_invalid_message",
            extra={
                "event": "task_progress_invalid_message",
                "task_id": task_id,
                "client_id": node_id,
                "message_type": type(raw).__name__,
            },
        )
        return None
    text = raw.strip()
    if not text:
        return None
    if len(text) > TASK_PROGRESS_MESSAGE_MAX_CHARS:
        logger.warning(
            "task_progress_message_truncated",
            extra={
                "event": "task_progress_message_truncated",
                "task_id": task_id,
                "client_id": node_id,
                "length": len(text),
            },
        )
        return text[:TASK_PROGRESS_MESSAGE_MAX_CHARS]
    return text


def _sanitize_progress_tool(raw: Any, task_id: str, node_id: str) -> str | None:
    """校验并规整当前工具名；非法类型告警并返回 None（不落库，ADR-061 M5a）。"""
    if raw is None:
        return None
    if not isinstance(raw, str):
        logger.warning(
            "task_progress_invalid_tool",
            extra={
                "event": "task_progress_invalid_tool",
                "task_id": task_id,
                "client_id": node_id,
                "tool_type": type(raw).__name__,
            },
        )
        return None
    text = raw.strip()
    if not text:
        return None
    if len(text) > TASK_PROGRESS_TOOL_MAX_CHARS:
        logger.warning(
            "task_progress_tool_truncated",
            extra={
                "event": "task_progress_tool_truncated",
                "task_id": task_id,
                "client_id": node_id,
                "length": len(text),
            },
        )
        return text[:TASK_PROGRESS_TOOL_MAX_CHARS]
    return text


def _sanitize_reported_text(
    payload: dict[str, Any],
    field: str,
    task_id: str,
    node_id: str,
) -> str | None:
    """校验并规整节点上报的长文本字段（plan_content / change_summary）。

    Args:
        payload: 完成事件 payload。
        field: 字段名（plan_content 或 change_summary）。
        task_id: 任务 ID（用于日志上下文）。
        node_id: 节点 ID（用于日志上下文）。

    Returns:
        合法文本；字段缺失时返回 ``None``；类型非法时返回 ``None`` 并记
        warning；超长时截断到 ``REPORTED_TEXT_MAX_LENGTH`` 并记 warning。
    """
    raw = payload.get(field)
    if not raw:
        return None
    if not isinstance(raw, str):
        logger.warning(
            f"task_completed_invalid_{field}",  # noqa: G004 - 事件名由固定白名单字段拼接
            extra={
                "event": f"task_completed_invalid_{field}",
                "task_id": task_id,
                "client_id": node_id,
                "value_type": type(raw).__name__,
            },
        )
        return None
    if len(raw) > REPORTED_TEXT_MAX_LENGTH:
        logger.warning(
            f"task_completed_{field}_truncated",  # noqa: G004
            extra={
                "event": f"task_completed_{field}_truncated",
                "task_id": task_id,
                "client_id": node_id,
                "original_length": len(raw),
                "truncated_length": REPORTED_TEXT_MAX_LENGTH,
            },
        )
        return raw[:REPORTED_TEXT_MAX_LENGTH]
    return raw


async def _handle_task_verify_blocked(node_id: str, payload: dict[str, Any]) -> None:
    """处理验收阻断事件（ADR-061 M4）：任务置 ``verify_blocked`` 并**主动找用户决定**。

    与 ``agent.task_failed`` 的区别：任务没有失败（代码已完成），只是必过层红或无法验证，
    且节点已用完自修轮次。中枢做三件事：① 落库 `verify_report` + 阻断时刻；
    ② 在任务关联的**构建会话**里追加一条助手消息（用户决策入口）；
    ③ 记审计。24h 无回应由调度器兜底置 `failed`。
    """
    task_id = payload.get("task_id")
    if not task_id:
        logger.warning(
            "task_verify_blocked_missing_task_id",
            extra={"event": "task_verify_blocked_missing_task_id", "client_id": node_id},
        )
        return

    report = payload.get("report")
    report = report if isinstance(report, dict) else {}
    reason = str(payload.get("reason") or "自动验收未通过")[:2000]

    async with AsyncSessionLocal() as session:
        task = await _get_verified_task(session, task_id, node_id)
        if task is None:
            return
        if task.status not in {TaskStatus.ASSIGNED.value, TaskStatus.RUNNING.value}:
            logger.warning(
                "task_verify_blocked_invalid_status",
                extra={
                    "event": "task_verify_blocked_invalid_status",
                    "task_id": task_id,
                    "client_id": node_id,
                    "status": task.status,
                },
            )
            return

        dispatch_backoff.clear_rejections(task_id)
        task.status = TaskStatus.VERIFY_BLOCKED.value
        task.verify_report = report or None
        task.verify_blocked_at = datetime.now(UTC)
        task.progress_message = f"验收未通过，等待你的决定：{reason}"[:500]
        await session.commit()

        rounds = report.get("repair_rounds")
        await _append_decision_message(session, task, reason, rounds)
        await tool_call_log_service.record(
            kind="agent_task_event",
            name="task_verify_blocked",
            task_id=task_id,
            result_summary={
                "agent_id": node_id,
                "verify_status": report.get("status"),
                "verify_source": report.get("source"),
                "must_pass_failures": report.get("must_pass_failures"),
                "repair_rounds": rounds,
            },
        )
        logger.warning(
            "task_verify_blocked",
            extra={
                "event": "task_verify_blocked",
                "task_id": task_id,
                "agent_id": node_id,
                "verify_status": report.get("status"),
                "repair_rounds": rounds,
            },
        )


async def _append_decision_message(
    session: AsyncSession,
    task: Task,
    reason: str,
    rounds: Any,
) -> None:
    """在任务构建会话里追加「等你决定」的助手消息（ADR-061 M4 主动找人）。

    失败仅告警：找不到会话不影响阻断态落库。
    """
    if task.session_id is None:
        return
    try:
        building = await session.get(TaskBuildingSession, task.session_id)
        if building is None:
            return
        messages = list(building.messages or [])
        rounds_text = f"（已自动自修 {rounds} 轮）" if isinstance(rounds, int) else ""
        messages.append(
            {
                "role": "assistant",
                "content": (
                    f"⚠️ 自动验收未通过，任务已暂停等你决定{rounds_text}。\n\n"
                    f"{reason}\n\n"
                    "请回复其中之一：\n"
                    "1. **继续修复** —— 我会让子 Agent 带着失败信息再修一轮；\n"
                    "2. **接受现状** —— 我会把任务标记为已完成（并记录验收未通过）。\n"
                    "（24 小时无回应将自动标记为失败）"
                ),
            }
        )
        building.messages = messages[-_DECISION_MESSAGE_KEEP :]
        await session.commit()
    except Exception:  # noqa: BLE001 — 主动通知是旁路，不得影响阻断落库
        logger.warning(
            "verify_blocked_decision_message_failed",
            extra={"event": "verify_blocked_decision_message_failed", "task_id": str(task.id)},
            exc_info=True,
        )


async def _handle_task_completed(node_id: str, payload: dict[str, Any]) -> None:
    """处理节点完成任务事件：任务状态变为 completed，节点恢复 idle。

    若 payload 携带非空 ``plan_content``（plan 模式方案全文）或
    ``change_summary``（build 模式本轮修改说明），分别写入任务对应字段；
    类型非法或超长时仅记录 warning（超长截断存储），不影响完成状态落库。

    注意：payload 中的 ``session_id``（tilacode 会话标识/项目键）不写入
    ``tasks.session_id``——该字段外键关联 ``task_building_sessions.id``，
    由创建任务流程确定，节点侧上报的值语义不符，写入必然外键违规。
    """
    task_id = payload.get("task_id")
    if not task_id:
        logger.warning(
            "task_completed_missing_task_id",
            extra={"event": "task_completed_missing_task_id", "client_id": node_id},
        )
        return

    async with AsyncSessionLocal() as session:
        task = await _get_verified_task(session, task_id, node_id)
        if task is None:
            return
        if task.status not in {TaskStatus.ASSIGNED.value, TaskStatus.RUNNING.value}:
            logger.warning(
                "task_completed_invalid_status",
                extra={
                    "event": "task_completed_invalid_status",
                    "task_id": task_id,
                    "client_id": node_id,
                    "status": task.status,
                },
            )
            return

        plan_content = _sanitize_reported_text(payload, "plan_content", task_id, node_id)
        change_summary = _sanitize_reported_text(payload, "change_summary", task_id, node_id)
        project_context = _sanitize_reported_text(payload, "project_context", task_id, node_id)

        # ADR-032：节点状态/负载由心跳派生维护，不再随任务终态改写节点行；
        # 任务终态后清除其回避记录
        dispatch_backoff.clear_rejections(task_id)

        # ADR-038：plan 模式任务上报方案 = 待人工确认（review），非终态；
        # build 任务 / 未上报方案的 plan 任务仍为 completed
        if task.execution_mode == "plan" and plan_content is not None:
            task.status = TaskStatus.REVIEW.value
        else:
            task.status = TaskStatus.COMPLETED.value
        task.progress = 100
        # 任务已终态：清除节点最后上报的「当前动作」（2026-10-07 用户实测：
        # 完成后动作卡仍显示「编写中/仍在执行」，误导任务还在跑）
        task.progress_stage = None
        task.progress_message = None
        task.progress_tool = None
        if plan_content is not None:
            task.plan_content = plan_content
        if change_summary is not None:
            task.change_summary = change_summary
        await session.commit()

        # ADR-066：任务终态自动唤醒编排——completed 后核对交付物/推进待办/汇报
        from app.services.orchestrator_wake_service import schedule_wake

        schedule_wake(str(task.id))

        # ADR-034：项目上下文记忆入库（链维度覆盖更新）+ 全量产物后台同步
        # （Agent 尚在线，拉取整目录 zip 作为链上最新现场，供跨 Agent 迁移）
        root = chain_root(task)
        if project_context is not None:
            await upsert_chain_context(session, root, project_context, task.id)
        _schedule_artifact_sync(task_id, node_id)

        await tool_call_log_service.record(
            kind="agent_task_event",
            name="task_completed",
            task_id=task_id,
            result_summary={
                "agent_id": node_id,
                "status": task.status,
                "plan_reported": plan_content is not None,
                "summary_reported": change_summary is not None,
                "context_reported": project_context is not None,
            },
        )
        logger.info(
            "task_completed",
            extra={
                "event": "task_completed",
                "task_id": task_id,
                "agent_id": node_id,
                "deliverable_count": 1,
                "status": task.status,
                "plan_reported": plan_content is not None,
                "summary_reported": change_summary is not None,
                "context_reported": project_context is not None,
            },
        )


async def _handle_task_failed(node_id: str, payload: dict[str, Any]) -> None:
    """处理节点任务失败事件：任务状态变为 failed，节点恢复 idle。

    仅处理 assigned / running 状态的任务；completed / failed / pending
    状态的任务仅记录 warning，不做任何变更。失败时保留当前 progress，
    便于前端展示任务中断位置。
    """
    task_id = payload.get("task_id")
    if not task_id:
        logger.warning(
            "task_failed_missing_task_id",
            extra={"event": "task_failed_missing_task_id", "client_id": node_id},
        )
        return

    async with AsyncSessionLocal() as session:
        task = await _get_verified_task(session, task_id, node_id)
        if task is None:
            return
        if task.status not in {TaskStatus.ASSIGNED.value, TaskStatus.RUNNING.value}:
            logger.warning(
                "task_failed_invalid_status",
                extra={
                    "event": "task_failed_invalid_status",
                    "task_id": task_id,
                    "client_id": node_id,
                    "status": task.status,
                },
            )
            return

        # ADR-032：任务终态清除回避记录；节点行不再随任务态改写
        dispatch_backoff.clear_rejections(task_id)

        task.status = TaskStatus.FAILED.value
        # 失败同样是终态：清除「当前动作」残留（同 task_completed，2026-10-07）
        task.progress_stage = None
        task.progress_message = None
        task.progress_tool = None
        await session.commit()

        # ADR-066：任务终态自动唤醒编排——failed 后汇报原因并推进待办
        from app.services.orchestrator_wake_service import schedule_wake

        schedule_wake(str(task.id))

        await tool_call_log_service.record(
            kind="agent_task_event",
            name="task_failed",
            task_id=task_id,
            status="failed",
            result_summary={"agent_id": node_id},
            error=str(payload.get("reason") or "unknown"),
        )
        logger.error(
            "task_failed",
            extra={
                "event": "task_failed",
                "task_id": task_id,
                "agent_id": node_id,
                "error_code": "TASK_EXECUTION_FAILED",
                "reason": payload.get("reason"),
            },
            exc_info=True,
        )


async def _handle_file_tree(node_id: str, payload: dict[str, Any]) -> None:
    """处理 agent.file_tree：保存清单快照并触发增量产物同步（ADR-028/059）。"""
    from app.services.artifact_service import artifact_service

    task_id = str(payload.get("task_id", ""))
    if not task_id:
        return
    files = payload.get("files") or []
    truncated = bool(payload.get("truncated", False))
    artifact_service.save_file_tree(task_id, files, truncated)
    # ADR-059 D4：执行中增量同步——离线时最多损失最近一轮（≤30s）的改动，
    # 而不是整段开发成果；完成时仍走全量 zip 兜底（见 _schedule_artifact_sync）。
    # 仅对在途任务开启，避免节点在终态后仍上报清单触发无谓拉取。
    if await _is_task_in_flight(task_id):
        _schedule_incremental_sync(task_id, node_id, files)


async def _is_task_in_flight(task_id: str) -> bool:
    """任务是否处于在途状态（assigned/running）——增量同步的前置闸门。"""
    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        return False
    async with AsyncSessionLocal() as session:
        status = await session.scalar(select(Task.status).where(Task.id == task_uuid))
    return status in (TaskStatus.ASSIGNED.value, TaskStatus.RUNNING.value)


async def _handle_file_chunk(node_id: str, payload: dict[str, Any]) -> None:
    """处理 agent.file_chunk：分块组装（ADR-028）。"""
    from app.services.artifact_service import artifact_service

    request_id = str(payload.get("request_id", ""))
    if not request_id:
        return
    artifact_service.handle_file_chunk(
        request_id=request_id,
        seq=int(payload.get("seq", 0)),
        total=int(payload.get("total", 0)),
        data_b64=str(payload.get("data", "")),
        done=bool(payload.get("done", False)),
    )


async def _handle_file_error(node_id: str, payload: dict[str, Any]) -> None:
    """处理 agent.file_error：取消拉取等待（ADR-028）。"""
    from app.services.artifact_service import artifact_service

    request_id = str(payload.get("request_id", ""))
    if not request_id:
        return
    artifact_service.handle_file_error(request_id, str(payload.get("reason", "unknown")))


async def _handle_log_chunk(node_id: str, payload: dict[str, Any]) -> None:
    """处理 agent.log_chunk：节点日志分块组装（ADR-047）。"""
    from app.services import tool_call_log_service

    request_id = str(payload.get("request_id", ""))
    if not request_id:
        return
    tool_call_log_service.handle_log_chunk(
        request_id=request_id,
        seq=int(payload.get("seq", 0)),
        total=int(payload.get("total", 0)),
        data_b64=str(payload.get("data", "")),
        done=bool(payload.get("done", False)),
    )


async def _handle_log_error(node_id: str, payload: dict[str, Any]) -> None:
    """处理 agent.log_error：节点日志拉取失败（ADR-047）。"""
    from app.services import tool_call_log_service

    request_id = str(payload.get("request_id", ""))
    if not request_id:
        return
    tool_call_log_service.handle_log_error(request_id, str(payload.get("reason", "unknown")))


def _coerce_uuid(raw: Any) -> uuid.UUID | None:
    """宽松解析 UUID 字符串；非法返回 None（上行 payload 不信任输入）。"""
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


async def _handle_node_chat_reply(node_id: str, payload: dict[str, Any]) -> None:
    """处理 agent.node_chat_reply（ADR-064 D1）：节点续聊回复全文落会话消息。

    续聊绕开任务状态机（ADR-064 D3），只校验任务存在、不校验分配关系；
    落库走 ``TaskBuildingSessionService.append_message``（旁路，失败不抛）。
    ``tokens`` 字段当前仅日志记录，不落库（按现有消息模型最小化）。
    """
    reply_to = payload.get("reply_to")
    task_id_raw = payload.get("task_id")
    text = _sanitize_reported_text(payload, "text", str(task_id_raw or ""), node_id)
    tokens = payload.get("tokens")
    if not reply_to or not task_id_raw or text is None:
        logger.warning(
            "node_chat_reply_invalid_payload",
            extra={
                "event": "node_chat_reply_invalid_payload",
                "client_id": node_id,
                "reply_to": str(reply_to) if reply_to else None,
                "task_id": str(task_id_raw) if task_id_raw else None,
            },
        )
        return
    task_uuid = _coerce_uuid(task_id_raw)
    if task_uuid is None:
        logger.warning(
            "node_chat_reply_invalid_task_id",
            extra={"event": "node_chat_reply_invalid_task_id", "client_id": node_id},
        )
        return

    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_uuid)
        if task is None:
            logger.warning(
                "node_chat_reply_task_not_found",
                extra={
                    "event": "node_chat_reply_task_not_found",
                    "client_id": node_id,
                    "task_id": str(task_uuid),
                },
            )
            return
        node_name = await _node_display_name(session, node_id)
        await TaskBuildingSessionService.append_message(
            session,
            task.session_id,
            {
                "role": "assistant",
                "content": text,
                "mention_node": node_name,
                "reply_to": str(reply_to),
                "ts": datetime.now(UTC).isoformat(),
            },
        )
    if tokens is not None:
        logger.info(
            "node_chat_reply_tokens",
            extra={
                "event": "node_chat_reply_tokens",
                "task_id": str(task_uuid),
                "client_id": node_id,
                "reply_to": str(reply_to),
                "tokens": tokens if isinstance(tokens, dict) else str(tokens)[:200],
            },
        )
    logger.info(
        "node_chat_reply_received",
        extra={
            "event": "node_chat_reply_received",
            "task_id": str(task_uuid),
            "client_id": node_id,
            "reply_to": str(reply_to),
            "text_length": len(text),
        },
    )


async def _handle_node_chat_error(node_id: str, payload: dict[str, Any]) -> None:
    """处理 agent.node_chat_error（ADR-064）：结构化告警 + 会话落用户可见错误消息。

    落库采用最小方案：``role="assistant"``、``content="[节点续聊失败] {reason}"``、
    ``mention_node``=节点显示名、带 ``reply_to`` 关联——前端按节点徽标渲染即可见，
    用户消息已在发起时落库不丢失（ADR-064 降级链路）。落库失败仅告警（旁路）。
    """
    reply_to = payload.get("reply_to")
    task_id_raw = payload.get("task_id")
    reason = str(payload.get("reason") or "unknown")[:200]
    logger.warning(
        "node_chat_error",
        extra={
            "event": "node_chat_error",
            "client_id": node_id,
            "task_id": str(task_id_raw) if task_id_raw else None,
            "reply_to": str(reply_to) if reply_to else None,
            "reason": reason,
        },
    )
    task_uuid = _coerce_uuid(task_id_raw) if task_id_raw else None
    if task_uuid is None:
        return

    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_uuid)
        if task is None:
            return
        node_name = await _node_display_name(session, node_id)
        message: dict[str, Any] = {
            "role": "assistant",
            "content": f"[节点续聊失败] {reason}",
            "mention_node": node_name,
            "ts": datetime.now(UTC).isoformat(),
        }
        if reply_to:
            message["reply_to"] = str(reply_to)
        await TaskBuildingSessionService.append_message(session, task.session_id, message)


_EVENT_HANDLERS: dict[str, Callable[[str, dict[str, Any]], Awaitable[None]]] = {
    AGENT_TASK_ACCEPTED: _handle_task_accepted,
    AGENT_TASK_REJECTED: _handle_task_rejected,
    AGENT_TASK_STARTED: _handle_task_started,
    AGENT_TASK_PROGRESS: _handle_task_progress,
    AGENT_TASK_COMPLETED: _handle_task_completed,
    AGENT_TASK_FAILED: _handle_task_failed,
    AGENT_TASK_VERIFY_BLOCKED: _handle_task_verify_blocked,
    AGENT_FILE_TREE: _handle_file_tree,
    AGENT_FILE_CHUNK: _handle_file_chunk,
    AGENT_FILE_ERROR: _handle_file_error,
    AGENT_LOG_CHUNK: _handle_log_chunk,
    AGENT_LOG_ERROR: _handle_log_error,
    AGENT_NODE_CHAT_REPLY: _handle_node_chat_reply,
    AGENT_NODE_CHAT_ERROR: _handle_node_chat_error,
}


async def handle_node_event(node_id: str, event: str, payload: dict[str, Any]) -> None:
    """分发并处理节点上报的任务相关事件。

    Args:
        node_id: 发送事件的节点 ID 字符串。
        event: 事件名称。
        payload: 事件 payload 字典。

    注意：
        所有异常均在函数内部捕获并记录，避免影响 WebSocket 连接稳定性。
        未知事件会被记录并忽略。
    """
    handler = _EVENT_HANDLERS.get(event)
    if handler is None:
        logger.info(
            "agent_ws_unknown_event",
            extra={
                "event": "agent_ws_unknown_event",
                "client_id": node_id,
                "event_name": event,
            },
        )
        return

    try:
        await handler(node_id, payload)
    except Exception:
        logger.exception(
            "task_event_handler_error",
            extra={
                "event": "task_event_handler_error",
                "client_id": node_id,
                "event_name": event,
            },
        )
