"""任务终态自动唤醒编排服务（ADR-066）。

任务到达终态（completed/failed）后自动向关联构建会话注入「[系统自动唤醒]」触发消息，
后台执行一轮编排（复用 ADR-033 技能链）：核对交付物 → todo_write 推进中枢待办 →
汇报结果 → 存在下一阶段时询问用户确认（不自动派发，对齐 ADR-033 准则 11）。

前置检查矩阵（全部通过才执行）：开关 → 会话关联 → build+终态类型 → 同链限流。
防环：同链 24h 唤醒 ≤5 次且连续失败唤醒 ≤3 次（超限记审计后放弃）。
落库：触发消息与 AI 回复经 ``TaskBuildingSessionService.append_message`` 追加
（前端打开会话或轮询即见，与 ADR-064 节点回复同款时序），失败静默降级不影响状态机。
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.base import AsyncSessionLocal
from app.models.task import Task
from app.models.task_building_session import TaskBuildingSession
from app.models.tool_call_log import ToolCallLog
from app.services import steering_service
from app.services.orchestrator_service import OrchestratorService
from app.services.system_settings_service import is_orchestrator_auto_wake_enabled
from app.services.task_build_rounds import BuildRoundCollector
from app.services.task_building_session_service import TaskBuildingSessionService

logger = logging.getLogger(__name__)

#: 唤醒审计在 tool_call_logs 中的 kind 标识
_WAKE_LOG_KIND = "orchestrator_wake"
#: 同一根任务链 24h 内自动唤醒次数上限
_CHAIN_WAKE_LIMIT_24H = 5
#: 同一根任务链「连续失败触发唤醒」次数上限（防失败→唤醒→重试死循环）
_FAILURE_WAKE_LIMIT = 3
#: 会话被前端流占用时的重试次数与间隔（秒）
_STEERING_RETRY_TIMES = 3
_STEERING_RETRY_INTERVAL_SECONDS = 10.0
#: 唤醒轮取会话历史的消息条数上限（对话骨架）
_HISTORY_TAIL_LIMIT = 12


def build_wake_message(
    *,
    title: str,
    terminal_status: str,
    detail: str | None,
    agent_todos_incomplete: list[str] | None = None,
) -> str:
    """构造注入会话的系统触发消息（UT-W1）。

    ``agent_todos_incomplete``：子 Agent 上报待办中**未完成**的内容列表
    （2026-10-07 用户实测补充）——任务状态可能被节点提前标记完成而子 Agent
    待办尚未收尾，此时明确提醒编排 AI 核实，不要轻信「已完成」。
    """
    state = "已完成" if terminal_status == "completed" else "执行失败"
    detail_line = detail or "（节点未附带说明）"
    lines = [
        f"[系统自动唤醒] 任务「{title}」{state}。",
        f"节点回报：{detail_line}",
    ]
    if agent_todos_incomplete:
        listing = "\n".join(f"  - {item}" for item in agent_todos_incomplete)
        lines.append(
            "⚠️ 注意：子 Agent 上报的待办清单还有以下未完成项"
            f"（共 {len(agent_todos_incomplete)} 项）——任务状态可能是节点提前收尾，"
            "并非真正完成：\n" + listing + "\n"
            "请先核实（可用 node_chat 向节点确认剩余项是否实际完成、或要求其补齐），"
            "**不要直接当作验收通过向用户汇报**；核实后据实更新中枢待办。"
        )
    lines.append(
        "请：1) 调用 get_task_status 核对任务状态与交付物；"
        "2) 用 todo_write 把中枢待办中的「跟踪执行」项标记完成，并把**可自主推进的"
        "待办逐项继续完成**（逐项 start → 执行 → complete，如核对交付物、自测、"
        "汇报），让用户在对话区实时看到你继续工作的过程；"
        "3) 只有需要用户决策/确认的事项（如部署目标确认、新任务派发确认）才停下来"
        "等待用户输入；"
        "4) 若存在下一阶段计划（如部署、迭代），列出并等待用户确认——"
        "未经用户确认不要创建或派发新任务，也不要对已完成任务重复派发。"
        "\n产物能力提示（ADR-068）：核对交付物用 list_artifacts（含 export.zip 与"
        "文件清单）；查看产物内代码/文档用 read_file；需要上传产物到服务器时，"
        "把 export.zip 作为宝塔 Upload 的 file_name 直接调用（中枢自动代执行本机"
        "上传），无需用户手动传包。"
    )
    return "\n".join(lines)


def _chain_wake_ok(
    *,
    chain_wake_count_24h: int,
    consecutive_failure_wakes: int,
) -> bool:
    """限流判定（UT-W2/W3）：同链 24h 次数与连续失败次数双重上限。"""
    if chain_wake_count_24h >= _CHAIN_WAKE_LIMIT_24H:
        return False
    return consecutive_failure_wakes < _FAILURE_WAKE_LIMIT


async def _load_wake_context(
    session: AsyncSession, task_id: str
) -> dict[str, Any] | None:
    """读取任务与限流上下文；任一前置不满足返回 None 并记日志。"""
    task = await session.get(Task, uuid.UUID(task_id))
    if task is None:
        logger.info(
            "wake_skip_task_missing", extra={"event": "wake_skip_task_missing", "task_id": task_id}
        )
        return None
    if task.status not in ("completed", "failed"):
        logger.info(
            "wake_skip_non_wake_status",
            extra={"event": "wake_skip_non_wake_status", "task_id": task_id, "status": task.status},
        )
        return None
    if task.execution_mode != "build":
        logger.info(
            "wake_skip_mode",
            extra={"event": "wake_skip_mode", "task_id": task_id, "mode": task.execution_mode},
        )
        return None
    if not await is_orchestrator_auto_wake_enabled(session):
        logger.info(
            "wake_skip_disabled", extra={"event": "wake_skip_disabled", "task_id": task_id}
        )
        return None
    session_id = task.session_id
    if session_id is None:
        logger.info(
            "wake_skip_no_session", extra={"event": "wake_skip_no_session", "task_id": task_id}
        )
        return None
    chat_session = await session.get(TaskBuildingSession, session_id)
    if chat_session is None:
        logger.info(
            "wake_skip_session_missing", extra={"event": "wake_skip_session_missing", "task_id": task_id}
        )
        return None

    # 限流：同链任务 id 集合（root_task_id 链上全部任务；task_id 审计列为 String）
    root = task.root_task_id or task.id
    chain_ids = [
        str(i)
        for i in (
            await session.execute(
                select(Task.id).where((Task.root_task_id == root) | (Task.id == root))
            )
        ).scalars().all()
    ]
    since = datetime.now(UTC) - timedelta(hours=24)
    chain_wake_count_24h = (
        await session.execute(
            select(func.count())
            .select_from(ToolCallLog)
            .where(
                ToolCallLog.kind == _WAKE_LOG_KIND,
                ToolCallLog.created_at >= since,
                ToolCallLog.task_id.in_(chain_ids),
            )
        )
    ).scalar_one()
    # 连续失败唤醒：同链最近 _FAILURE_WAKE_LIMIT 条唤醒记录的 name 是否全为 failure
    recent = (
        await session.execute(
            select(ToolCallLog.name)
            .where(
                ToolCallLog.kind == _WAKE_LOG_KIND,
                ToolCallLog.created_at >= since,
                ToolCallLog.task_id.in_(chain_ids),
            )
            .order_by(ToolCallLog.created_at.desc())
            .limit(_FAILURE_WAKE_LIMIT)
        )
    ).scalars().all()
    consecutive_failure_wakes = 0
    for name in recent:
        if name == "failed":
            consecutive_failure_wakes += 1
        else:
            break
    if not _chain_wake_ok(
        chain_wake_count_24h=chain_wake_count_24h,
        consecutive_failure_wakes=consecutive_failure_wakes,
    ):
        logger.warning(
            "wake_skip_rate_limited",
            extra={
                "event": "wake_skip_rate_limited",
                "task_id": task_id,
                "root_task_id": str(root),
                "chain_wake_count_24h": chain_wake_count_24h,
                "consecutive_failure_wakes": consecutive_failure_wakes,
            },
        )
        return None

    return {
        "task_id": task_id,
        "title": task.title,
        "terminal_status": task.status,
        "change_summary": task.change_summary,
        "error_hint": task.last_reject_reason,
        "session_id": str(session_id),
        "root_task_id": str(root),
        "specification": task.specification,
        # 子 Agent 待办未完成明细（2026-10-07 用户实测：节点提前收尾报完成，
        # 子 Agent 待办 6/8——唤醒轮必须知道这一矛盾，不能轻信「已完成」）
        "agent_todos_incomplete": [
            str(todo.get("content") or "").strip()
            for todo in (task.todos or [])
            if isinstance(todo, dict) and todo.get("status") != "completed"
        ],
    }


def _history_tail(messages: list[dict[str, Any]], limit: int) -> list[dict[str, str]]:
    """取会话消息尾部的对话骨架（role/content），供编排 prompt 使用。"""
    tail: list[dict[str, str]] = []
    for msg in messages:
        role = msg.get("role")
        content = str(msg.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            tail.append({"role": role, "content": content})
    return tail[-limit:]


async def _execute_wake_round(context: dict[str, Any]) -> None:
    """执行唤醒编排轮：注入触发消息 → 后台消费事件流 → 落库。"""
    session_id = context["session_id"]
    wake_message = build_wake_message(
        title=context["title"],
        terminal_status=context["terminal_status"],
        detail=(
            context.get("change_summary")
            or context.get("error_hint")
            or ("任务执行失败" if context["terminal_status"] == "failed" else None)
        ),
        agent_todos_incomplete=context.get("agent_todos_incomplete") or None,
    )
    # 会话被前端流占用 → 延迟重试（IT-W5 场景由测试覆盖）
    for attempt in range(_STEERING_RETRY_TIMES + 1):
        if not steering_service.is_active(session_id):
            break
        if attempt == _STEERING_RETRY_TIMES:
            logger.warning(
                "wake_steering_busy_giveup",
                extra={"event": "wake_steering_busy_giveup", "session_id": session_id},
            )
            return
        await asyncio.sleep(_STEERING_RETRY_INTERVAL_SECONDS)

    async with AsyncSessionLocal() as session:
        session_service = TaskBuildingSessionService(session)
        # 触发消息先落库（作为唤醒轮的 user 消息），旁路原则失败静默
        await session_service.append_message(
            session,
            uuid.UUID(session_id),
            {
                "role": "user",
                "content": wake_message,
                "source": "system_wake",
                "ts": datetime.now(UTC).isoformat(),
            },
        )
        # 会话历史尾部（对话骨架）作为编排 prompt 前文
        chat_session = await session_service.get(uuid.UUID(session_id))
        history = _history_tail(
            list(chat_session.messages or []) if chat_session else [],
            _HISTORY_TAIL_LIMIT - 1,
        )
        prompt_messages = [*history, {"role": "user", "content": wake_message}]

        service = OrchestratorService(session)
        collector = BuildRoundCollector()
        ai_reply_chunks: list[str] = []
        model = ""
        mode = "thinking"
        started = time.monotonic()
        try:
            steering_service.open_stream(session_id)
            # ADR-066 唤醒可视化（2026-10-07 用户裁决）：打开广播队列——前端
            # SSE 订阅端点实时接收本轮事件，像正常对话一样流式展示
            from app.services import wake_stream_service

            wake_stream_service.open_stream(session_id)
            # 2026-10-08 用户实测：部署 13 步远超默认 40 轮 + 600s，唤醒轮被
            # 强制收尾掐断长流程——独立配置更大的轮数与超时窗口
            settings = get_settings()
            async with asyncio.timeout(settings.orchestrator_wake_timeout_seconds):
                async for event in service.chat_stream(
                    prompt_messages,
                    context.get("specification"),
                    mode=mode,
                    session_id=session_id,
                    parent_task_id=context.get("root_task_id"),
                    max_rounds_override=settings.orchestrator_wake_max_tool_rounds,
                ):
                    event_name = event.get("event")
                    event_data = event.get("data") or {}
                    # 内部事件（下划线前缀）不广播——前端渲染协议不含它们
                    if event_name and not event_name.startswith("_"):
                        wake_stream_service.publish(session_id, event)
                    if event_name == "meta":
                        model = str(event_data.get("model") or model)
                    elif event_name == "chunk":
                        ai_reply_chunks.append(str(event_data.get("delta") or ""))
                    elif event_name == "thinking":
                        collector.add_thinking(str(event_data.get("delta") or ""))
                    elif event_name == "step_started":
                        collector.start_step(event_data)
                    elif event_name == "step_finished":
                        collector.finish_step(event_data)
                    elif event_name == "_history_trajectory":
                        # 唤醒轮回复以单条 assistant 消息落库，轨迹仅用于
                        # 闭合回合（thinking/steps 已由 collector 收集）
                        collector.close_round()
                    elif event_name == "error":
                        logger.warning(
                            "wake_round_llm_error",
                            extra={
                                "event": "wake_round_llm_error",
                                "session_id": session_id,
                                "message": str(event_data.get("message"))[:200],
                            },
                        )
        except TimeoutError:
            logger.warning(
                "wake_round_timeout",
                extra={"event": "wake_round_timeout", "session_id": session_id},
            )
        finally:
            from app.services import wake_stream_service

            wake_stream_service.close_stream(session_id)
            steering_service.close_stream(session_id)

        ai_reply = "".join(ai_reply_chunks).strip()
        rounds = collector.rounds
        # 落库 AI 回复（thinking/steps 平铺附加，前端既有渲染兼容）
        if ai_reply or rounds:
            final_thinking = "".join(r["thinking"] for r in rounds)
            final_steps = [step for r in rounds for step in r["steps"]]
            assistant_message: dict[str, Any] = {"role": "assistant", "content": ai_reply}
            if final_thinking:
                assistant_message["thinking"] = final_thinking
            if final_steps:
                assistant_message["steps"] = final_steps
            await session_service.append_message(
                session, uuid.UUID(session_id), assistant_message
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "wake_round_completed",
            extra={
                "event": "wake_round_completed",
                "session_id": session_id,
                "task_id": context["task_id"],
                "root_task_id": context["root_task_id"],
                "trigger": context["terminal_status"],
                "reply_length": len(ai_reply),
                "model": model,
                "duration_ms": duration_ms,
            },
        )


async def _wake_with_guard(task_id: str) -> None:
    """唤醒守卫：前置检查 → 审计 → 执行；任何异常只记日志不上抛。"""
    try:
        async with AsyncSessionLocal() as session:
            context = await _load_wake_context(session, task_id)
        if context is None:
            return
        # 唤醒审计（限流数据源）：落一条 kind=orchestrator_wake 记录
        # （result_summary 为 Text 列，root_task_id 以 JSON 字符串存储）
        async with AsyncSessionLocal() as session:
            session.add(
                ToolCallLog(
                    kind=_WAKE_LOG_KIND,
                    name="failed" if context["terminal_status"] == "failed" else "completed",
                    status="success",
                    task_id=task_id,
                    result_summary=json.dumps(
                        {
                            "root_task_id": context["root_task_id"],
                            "session_id": context["session_id"],
                            "trigger": context["terminal_status"],
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            await session.commit()
        await _execute_wake_round(context)
    except Exception:  # noqa: BLE001 — 唤醒失败静默降级，不影响任务状态机（红线②）
        logger.exception(
            "wake_round_failed", extra={"event": "wake_round_failed", "task_id": task_id}
        )


def schedule_wake(task_id: str) -> None:
    """任务终态钩子入口：fire-and-forget 调度唤醒轮（不阻塞 WS 事件处理）。"""
    if not get_settings().orchestrator_auto_wake_enabled:
        return
    try:
        asyncio.create_task(
            _wake_with_guard(task_id), name=f"orchestrator-wake-{task_id}"
        )
    except RuntimeError:
        # 无运行事件循环（如测试同步上下文）时忽略
        logger.warning(
            "wake_schedule_no_loop", extra={"event": "wake_schedule_no_loop", "task_id": task_id}
        )
