"""任务调度器服务。

后台周期性扫描 ``tasks`` 表中 ``pending`` 状态的任务，根据 ``agent_type``
匹配在线且资源合格的 Agent 节点，将任务分配给最合理的节点。

除派发外，调度器还承担「任务卡死兜底」职责（见 ADR-015 / spec/task-stale-detector-spec.md）：
每轮扫描 ``status='running' AND updated_at < NOW() - stale_task_timeout_seconds`` 的任务，
自动标记为 ``failed`` 并释放节点。

派发模式（ADR-017）：调度器只接管 ``dispatch_type='auto'`` 且手动派发窗口
已过的任务；``dispatch_type='manual'`` 或窗口未到期的任务被跳过。

AI 智能调度（ADR-033）：候选预筛后——候选唯一走快速路径直接分配（零 LLM
开销）；多候选且开关开启时批量 AI 决策（白名单校验，失败回退负载排序且
不重试）；决策全程落 ``dispatch_decisions`` 审计表。

注：本文件超过 300 行上限（见 docs/standards/coding-standards.md 4.3）。
派发扫描与卡死兜底共享调度周期、会话管理与节流机制，强耦合于同一事件循环，
拆分会引入重复的调度基础设施，故按豁免场景保留并在此说明。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import contextvars

from app.core.config import get_settings
from app.models.agent_node import AgentNode, NodeStatus
from app.models.base import AsyncSessionLocal
from app.models.llm_provider import LLMProvider
from app.models.task import DISPATCHABLE_STATUSES, Task, TaskStatus
from app.services import dispatch_backoff, permission_policy_service, tool_call_log_service
from app.services.attachment_service import AttachmentService
from app.services.connection_manager import connection_manager
from app.services.dispatch_decision_service import (
    NodeDecision,
    build_candidate_snapshot,
    build_hold_reason,
    decide_assignments,
    diagnose_eligibility,
    eligibility_gap,
    is_ai_dispatch_enabled,
    list_eligible_nodes,
    node_available_memory_gb,
    node_load_score,
    record_decision,
    running_task_counts,
)
from app.services.llm_provider_service import LLMProviderService
from app.services.standard_doc_service import build_rule_docs_payload
from app.services.task_handoff_service import build_handoff_payload

logger = logging.getLogger(__name__)

# 默认值（仅测试与文档参考用）；运行期以 Pydantic Settings 为准
# （environment-config-standards §5.2：调度类阈值必须可经环境变量配置）
SCHEDULER_INTERVAL_SECONDS = 10

#: 任务排队原因的上次提示时间（进程内节流，ADR-059 D3）
_hold_notified_at: dict[str, datetime] = {}

# 任务卡死兜底自动写入的失败原因（见 ADR-015）
_STALE_FAIL_REASON_TEMPLATE = "任务执行超过 {timeout}s 无进度，自动标记为 failed（卡死兜底）"


def _bind_request_id() -> None:
    """将当前上下文的 request_id 绑定到日志上下文。"""
    request_id = contextvars.get_contextvars().get("request_id")
    if request_id:
        contextvars.bind_contextvars(request_id=request_id)


async def _assign_task_to_node(
    session: AsyncSession,
    task: Task,
    node: AgentNode,
) -> None:
    """将任务分配给指定节点并更新任务状态。

    ADR-032：节点不再随分配置 ``busy``、负载不再 +1——节点状态由心跳
    上报派生（在跑数 > 0 即 busy 展示态），负载由心跳综合分维护。

    Args:
        session: 异步数据库会话。
        task: 待分配的任务。
        node: 被选中的可调度节点。
    """
    task.status = TaskStatus.ASSIGNED.value
    task.assigned_node_id = node.id
    # ADR-059：派发成功即清除排队原因；强行派送标记保留至任务结束供审计
    task.dispatch_hold_reason = None
    # 显式保留 dispatch_type=auto：模型默认就是 auto，但显式写更稳妥
    # （避免未来默认值变更时把已分配的任务变成 manual，破坏审计）
    if task.dispatch_type != "auto":
        task.dispatch_type = "auto"
    # 分配后窗口意义已结束（任务已不再 pending），显式清空
    task.dispatch_window_until = None

    logger.info(
        "task_auto_dispatched",
        extra={
            "event": "task_auto_dispatched",
            "task_id": str(task.id),
            "agent_id": str(node.id),
            "agent_type": node.agent_type,
            "agent_load": node.load,
            "dispatch_type": task.dispatch_type,
            "target_node_id": str(task.target_node_id) if task.target_node_id else None,
            "forced_dispatch": bool(task.forced_dispatch),
        },
    )
    if task.forced_dispatch:
        # 强行派送是唯一允许越过准入门槛的路径，必须留可追溯记录（ADR-059 D2）
        logger.warning(
            "task_forced_dispatched",
            extra={
                "event": "task_forced_dispatched",
                "task_id": str(task.id),
                "agent_id": str(node.id),
                "available_memory_gb": node_available_memory_gb(node),
                "load_score": node_load_score(node),
                "reason": "用户明确要求越过准入门槛强行派送（forced_dispatch=true）",
            },
        )


def _build_dispatch_message(task: Task) -> dict[str, Any]:
    """构造向节点下发的任务消息。

    Args:
        task: 已分配的任务。

    Returns:
        符合 ``hub.task_dispatch`` 事件格式的消息字典。
    """
    return {
        "event": "hub.task_dispatch",
        "payload": {
            "task_id": str(task.id),
            "task_name": task.title,
            "description": task.description,
            "specification": task.specification,
            "mode": getattr(task, "execution_mode", "build"),
            "base_task_id": str(task.root_task_id or task.id),
            "change_brief": task.change_brief,
            # 任务产物保存路径（ADR-028）；缺省时 Agent 按默认规则分配
            "output_dir": getattr(task, "output_dir", None),
            # 环境要求清单（ADR-048 P2）：内联下发，子节点 AI 首次开发时检查并安装缺失项
            "environment_requirements": getattr(task, "environment_requirements", None) or [],
        },
        "timestamp": datetime.now(UTC).isoformat(),
        "request_id": None,
    }


async def _pick_vision_model_row(session: AsyncSession) -> LLMProvider | None:
    """挑选勾选支持图片输入的自定义模型行（ADR-039 自动选模候选）。

    使用中行优先，其次按创建时间取最早的一行。
    """
    result = await session.execute(
        select(LLMProvider)
        .where(LLMProvider.supports_vision.is_(True))
        .order_by(LLMProvider.is_active.desc(), LLMProvider.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _build_executor_model_entry(
    session: AsyncSession,
    task: Task,
    llm_cache: dict[str, Any],
    has_attachments: bool = False,
) -> dict[str, Any]:
    """构造派发 payload 的 executor_model 条目（ADR-029 / ADR-030 / ADR-039 增强）。

    ``hub`` 来源时实时读取 system_settings 的使用中配置并注入凭据
    （仅随 WS 消息内存传输，不落库不落日志）；``custom`` 来源按
    provider_id 读取指定模型行注入（行已删除则降级不带凭据并告警）；
    ``auto`` 来源带图任务优先派给勾选支持图片的自定义模型行，无候选
    降级使用中模型并告警。``supports_vision`` 随 payload 下发，Agent
    据此决定 send_prompt 是否携带图片 parts（不支持时附件仅落盘）。
    配置缺失时统一降级为不带凭据（Agent 回退内置默认免费通道）。

    Args:
        session: 异步数据库会话。
        task: 待派发任务。
        llm_cache: 调度循环内复用的 LLM 配置缓存（首次读取后缓存）。
        has_attachments: 任务是否携带图片附件（ADR-039 自动选模判定）。

    Returns:
        executor_model 字典：{source, model, supports_vision, provider?, api_key?, base_url?}。
    """
    selection = task.executor_model or {"source": "hub"}
    source = selection.get("source", "hub")
    entry: dict[str, Any] = {"source": source, "model": selection.get("model")}
    if source == "auto":
        # ADR-039 自动选模：带图 → 视觉模型候选；无候选降级使用中模型
        if has_attachments:
            vision_row = await _pick_vision_model_row(session)
            if vision_row is not None:
                entry.update(
                    {
                        "source": "custom",
                        "model": vision_row.model,
                        "provider": vision_row.provider,
                        "api_key": vision_row.api_key,
                        "base_url": vision_row.base_url,
                        "supports_vision": True,
                    }
                )
                logger.info(
                    "executor_model_auto_picked_vision",
                    extra={
                        "event": "executor_model_auto_picked_vision",
                        "task_id": str(task.id),
                        "provider_id": str(vision_row.id),
                        "model": vision_row.model,
                    },
                )
                return entry
            logger.warning(
                "executor_model_auto_no_vision_candidate",
                extra={
                    "event": "executor_model_auto_no_vision_candidate",
                    "task_id": str(task.id),
                    "reason": "任务带图片附件但未勾选任何支持图片的模型，降级使用中模型",
                },
            )
        source = "hub"
        entry["source"] = "hub"
    if source == "custom":
        provider_id = selection.get("provider_id")
        if not provider_id:
            logger.warning(
                "executor_model_custom_id_missing",
                extra={"event": "executor_model_custom_id_missing", "task_id": str(task.id)},
            )
            return entry
        from uuid import UUID

        try:
            row = await session.get(LLMProvider, UUID(str(provider_id)))
        except ValueError:
            row = None
        if row is None:
            logger.warning(
                "executor_model_custom_row_missing",
                extra={
                    "event": "executor_model_custom_row_missing",
                    "task_id": str(task.id),
                    "provider_id": str(provider_id),
                },
            )
            return entry
        entry["provider"] = row.provider
        entry["model"] = row.model or entry.get("model")
        entry["api_key"] = row.api_key
        entry["base_url"] = row.base_url
        entry["supports_vision"] = bool(row.supports_vision)
        return entry
    if source != "hub":
        return entry

    cfg = llm_cache.get("config")
    if cfg is None and llm_cache.get("error") is None:
        try:
            cfg = await LLMProviderService().get_active_provider(session)
            llm_cache["config"] = cfg
        except ValueError as exc:
            llm_cache["error"] = str(exc)
            logger.warning(
                "executor_model_hub_config_missing",
                extra={
                    "event": "executor_model_hub_config_missing",
                    "task_id": str(task.id),
                    "reason": str(exc),
                },
            )
    if cfg is not None:
        entry["provider"] = cfg.provider
        entry["model"] = entry.get("model") or cfg.model
        entry["api_key"] = cfg.api_key
        entry["base_url"] = cfg.base_url
        entry["supports_vision"] = bool(cfg.supports_vision)
    else:
        entry["supports_vision"] = False
    return entry


async def _send_dispatch_to_node(node_id: str, message: dict[str, Any]) -> None:
    """通过 WebSocket 向节点下发任务，发送失败仅记录日志不阻塞。

    Args:
        node_id: 目标节点 ID。
        message: 待发送的消息字典。
    """
    try:
        sent = await connection_manager.send_to_node(node_id, message)
        if not sent:
            logger.warning(
                "task_dispatch_websocket_not_sent",
                extra={
                    "task_id": message["payload"]["task_id"],
                    "client_id": node_id,
                },
            )
    except Exception:
        logger.exception(
            "task_dispatch_websocket_failed",
            extra={
                "task_id": message["payload"]["task_id"],
                "client_id": node_id,
            },
        )


def _quick_path_decision(task: Task, node: AgentNode, reason: str | None = None) -> NodeDecision:
    """构造候选唯一快速路径（或指定节点/规则回退）的规则决策审计记录。

    跳过 LLM，零 token（ADR-033）；ADR-059 起指定节点与强行派送也走此路径，
    通过 ``reason`` 记录具体来由以便审计回溯。
    """
    return NodeDecision(
        task_id=str(task.id),
        node=node,
        final_node=node,
        reason=reason or "候选唯一，快速路径直接分配（跳过 LLM）",
        decision_source="rule",
        candidates_snapshot=build_candidate_snapshot([node]),
    )


async def _classify_pending_tasks(
    session: AsyncSession,
    pending_tasks: list[Task],
) -> tuple[
    list[tuple[Task, AgentNode, str]],
    list[tuple[Task, AgentNode]],
    dict[str, list[AgentNode]],
    list[tuple[Task, str]],
]:
    """对 pending 任务做候选预筛分类（ADR-032 同口径，ADR-059 增双门槛）。

    分类优先级（ADR-059 D2）：

    1. **指定节点任务**（``target_node_id`` 非空）→ ``target_assign`` 或
       ``hold``：指定即承诺，只等该节点，不经 AI、不改派他处；
    2. 无指定且 ``len(eligible) == 1`` → 快速路径直达（跳过 LLM）；
    3. 无指定且 ``len(eligible) >= 2`` 且 AI 开启 → AI 在 eligible 内决策；
    4. 无指定且 AI 关闭 → 规则选负载最低的 eligible；
    5. 无 eligible 节点 → ``hold``（排队 + 缺口提示，不静默、不硬塞）。

    Args:
        session: 异步数据库会话。
        pending_tasks: 本轮扫描到的 pending 任务（已加锁）。

    Returns:
        (快速路径 [(task, node, reason)]，规则分配 [(task, node)]，
        AI 候选 task_id → eligible 节点，排队 [(task, hold_reason)])。
    """
    ai_enabled = await is_ai_dispatch_enabled(session)
    quick_assign: list[tuple[Task, AgentNode, str]] = []
    rule_assign: list[tuple[Task, AgentNode]] = []
    ai_candidates: dict[str, list[AgentNode]] = {}
    holds: list[tuple[Task, str]] = []
    for task in pending_tasks:
        excluded = dispatch_backoff.excluded_nodes(str(task.id))
        if task.target_node_id is not None:
            outcome = await _resolve_target_task(session, task, excluded)
            if isinstance(outcome, tuple):
                node, reason = outcome
                quick_assign.append((task, node, reason))
            else:
                holds.append((task, outcome))
            continue
        eligible = await list_eligible_nodes(session, task.agent_type, excluded)
        if not eligible:
            diagnostics = await diagnose_eligibility(session, task.agent_type, excluded)
            holds.append((task, build_hold_reason(diagnostics)))
            logger.warning(
                "no_eligible_agent",
                extra={
                    "event": "no_eligible_agent",
                    "task_id": str(task.id),
                    "agent_type": task.agent_type,
                    "dispatch_type": task.dispatch_type,
                    "candidates": len(diagnostics),
                },
            )
            continue
        if len(eligible) == 1:
            quick_assign.append((task, eligible[0], "候选唯一，快速路径直接分配（跳过 LLM）"))
        elif ai_enabled:
            ai_candidates[str(task.id)] = eligible
        else:
            rule_assign.append((task, eligible[0]))
    return quick_assign, rule_assign, ai_candidates, holds


async def _resolve_target_task(
    session: AsyncSession,
    task: Task,
    excluded: set[str],
) -> tuple[AgentNode, str] | str:
    """处理「已指定目标节点」的任务（ADR-059 D2）。

    Returns:
        可派发时返回 ``(node, reason)``；否则返回排队原因字符串。
    """
    node = await session.get(AgentNode, task.target_node_id)
    if node is None:
        return "指定节点已不存在（可能已被移除），请改派或取消指定"
    online = node.status in (NodeStatus.IDLE.value, NodeStatus.BUSY.value)
    if not online:
        if task.forced_dispatch:
            # 强行派送例外：仍要求节点在线（离线派发无意义，只会白等超时）
            return f"指定节点「{node.name}」离线，无法强行派送；等待恢复或改派"
        return f"指定节点「{node.name}」离线，等待恢复或改派"
    counts = await running_task_counts(session, [node.id])
    used = counts.get(str(node.id), 0)
    slots_free = max(int(node.max_concurrent_tasks or 0) - used, 0)
    gap = eligibility_gap(node)
    if slots_free <= 0:
        return f"指定节点「{node.name}」并发槽位已满（{used}/{node.max_concurrent_tasks}）"
    if gap is not None and not task.forced_dispatch:
        return f"指定节点「{node.name}」暂不可自主：{gap}（可等待或要求强行派送）"
    if str(node.id) in excluded and not task.forced_dispatch:
        return f"指定节点「{node.name}」刚拒单，回避中（5 分钟内不再派往）"
    reason = (
        f"指定节点「{node.name}」，用户要求强行派送（审计 forced_dispatch=true）"
        if task.forced_dispatch
        else f"指定节点「{node.name}」已达准入水位，按指定派送（跳过 AI）"
    )
    return node, reason


async def _apply_holds(session: AsyncSession, holds: list[tuple[Task, str]]) -> int:
    """写入排队原因并在节流窗口内提示用户（ADR-059 D3）。

    Returns:
        实际发生变化的 hold 数量（原因文本变更或首次置位）。
    """
    interval = get_settings().dispatch_hold_notify_interval_seconds
    now = datetime.now(UTC)
    changed = 0
    for task, reason in holds:
        reason = reason[:200]
        previous = task.dispatch_hold_reason
        task.dispatch_hold_reason = reason
        if previous == reason:
            continue
        changed += 1
        last_notified = _hold_notified_at.get(str(task.id))
        if last_notified is not None and (now - last_notified).total_seconds() < interval:
            continue
        _hold_notified_at[str(task.id)] = now
        logger.warning(
            "task_dispatch_held",
            extra={
                "event": "task_dispatch_held",
                "task_id": str(task.id),
                "reason": reason,
                "target_node_id": str(task.target_node_id) if task.target_node_id else None,
                "forced_dispatch": bool(task.forced_dispatch),
            },
        )
    return changed


async def _assign_plan(
    session: AsyncSession,
    plan: list[tuple[Task, AgentNode, NodeDecision | None]],
) -> tuple[int, list[tuple[str, dict[str, Any]]]]:
    """按计划锁定节点行并分配任务，构造 WS 下发消息。

    Returns:
        (成功分配数量，[(node_id, message)])。分配数量为 0 时调用方
        回滚事务（丢弃审计记录，行锁随会话关闭释放）。
    """
    dispatch_messages: list[tuple[str, dict[str, Any]]] = []
    llm_cache: dict[str, Any] = {}
    dispatched_count = 0
    # 权限策略（ADR-061 M2b，Q1 全局单条）：整批派发只读一次，随每条 payload 下发
    permission_policy = await permission_policy_service.load_policy(session)
    for task, node, _decision in plan:
        locked_node = await session.get(AgentNode, node.id, with_for_update=True)
        if locked_node is None:
            logger.warning(
                "task_dispatch_node_vanished",
                extra={"event": "task_dispatch_node_vanished", "node_id": str(node.id)},
            )
            continue
        await _assign_task_to_node(session, task, locked_node)
        message = _build_dispatch_message(task)
        # 权限策略（ADR-061 M2b）：节点以派发的策略为权威，本地配置为缓存
        message["payload"]["permission_policy"] = permission_policy
        # 任务附件（ADR-039）：为附件签发一次性下载 token 随 payload 下发
        attachments = await AttachmentService().issue_task_payload(session, task.id)
        if attachments:
            message["payload"]["attachments"] = attachments
        # 执行模型与凭据注入（ADR-029）；循环内缓存 LLM 配置避免重复查询；
        # 带图任务时自动选模优先视觉模型（ADR-039）
        message["payload"]["executor_model"] = await _build_executor_model_entry(
            session, task, llm_cache, has_attachments=bool(attachments)
        )
        # 现场迁移（ADR-034）：链上有历史现场时附带 handoff（打包在线程池，
        # 失败/无现场返回 None，派发照常）
        handoff = await build_handoff_payload(session, task)
        if handoff is not None:
            message["payload"]["handoff"] = handoff
        # 规则文档（ADR-048）：规范正文+元规则自动附加（含清单渲染与环境检查文档）；
        # 传入目标节点以按其环境签名召回历史经验（ADR-061 M6）
        rule_docs = await build_rule_docs_payload(session, task, locked_node)
        if rule_docs:
            message["payload"]["rule_docs"] = rule_docs
        dispatch_messages.append((str(locked_node.id), message))
        dispatched_count += 1
    return dispatched_count, dispatch_messages


async def dispatch_pending_tasks(session: AsyncSession) -> int:
    """扫描并自动分配所有窗口已过且派发模式为 auto 的 pending 任务（ADR-017/033）。

    流程：扫描加锁 → 候选预筛三路分类 → （AI 开启时）多候选批量 LLM 决策 →
    锁定节点行逐任务分配 → 审计与任务状态同一事务提交 → WS 下发。

    Args:
        session: 异步数据库会话。

    Returns:
        本次成功分配的任务数量。
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(Task)
        .where(
            # ADR-059 D5：只派发 dispatchable 状态——`requeue_pending`
            # （待确认改派）必须被排除，否则「先询问」退化为自动改派
            Task.status.in_(DISPATCHABLE_STATUSES),
            Task.dispatch_type == "auto",
            # ADR-067 派发确认门禁：编排创建（等待用户确认派发）的任务跳过
            Task.dispatch_hold.is_(False),
            or_(
                Task.dispatch_window_until.is_(None),
                Task.dispatch_window_until <= now,
            ),
        )
        .order_by(Task.created_at.asc())
        .with_for_update(),
    )
    pending_tasks = list(result.scalars().all())
    if not pending_tasks:
        return 0

    quick_assign, rule_assign, ai_candidates, holds = await _classify_pending_tasks(
        session, pending_tasks
    )
    decisions: dict[str, NodeDecision] = {}
    if ai_candidates:
        decisions = await decide_assignments(session, ai_candidates)

    batch_id = uuid.uuid4() if decisions else None
    for task, node, reason in quick_assign:
        session.add(record_decision(_quick_path_decision(task, node, reason=reason)))
    for decision in decisions.values():
        session.add(record_decision(decision, batch_id=batch_id))

    plan: list[tuple[Task, AgentNode, NodeDecision | None]] = [
        *[(task, node, None) for task, node, _reason in quick_assign],
        *[(task, node, None) for task, node in rule_assign],
    ]
    for task_id, decision in decisions.items():
        task = next((t for t in pending_tasks if str(t.id) == task_id), None)
        if task is not None and decision.final_node is not None:
            plan.append((task, decision.final_node, decision))

    dispatched_count, dispatch_messages = await _assign_plan(session, plan)
    # ADR-059 D3：排队原因落库（同一事务提交），供任务列表/详情展示
    await _apply_holds(session, holds)
    if dispatched_count or holds:
        await session.commit()
    if dispatched_count:
        for node_id, message in dispatch_messages:
            await _send_dispatch_to_node(node_id, message)
            payload = message.get("payload", {})
            await tool_call_log_service.record(
                kind="dispatch",
                name=str(payload.get("assigned_node_name") or node_id),
                task_id=str(payload.get("task_id") or ""),
                result_summary={
                    "dispatch_type": "auto",
                    "agent_id": node_id,
                    **permission_policy_service.audit_summary(payload.get("permission_policy")),
                },
            )
    # 无任务被分配且无排队变化时不 commit：审计草稿与行锁随会话关闭/回滚自然丢弃
    # （避免 rollback 使已加载 ORM 对象 expire，破坏调用方后续属性访问）

    logger.info(
        "dispatch_pending_tasks_completed",
        extra={
            "event": "dispatch_pending_tasks_completed",
            "pending_task_count": len(pending_tasks),
            "dispatched_count": dispatched_count,
            "quick_path_count": len(quick_assign),
            "ai_decided_count": len(decisions),
            "held_count": len(holds),
            "window_now": now.isoformat(),
        },
    )
    return dispatched_count


async def _dispatch_once(session: AsyncSession | None = None) -> int:
    """使用独立会话执行一次任务调度。

    Args:
        session: 测试场景可注入已存在的会话；缺省时使用
            :data:`AsyncSessionLocal` 新建会话。

    Returns:
        本次成功分配的任务数量。
    """
    if session is not None:
        return await dispatch_pending_tasks(session)
    async with AsyncSessionLocal() as new_session:
        return await dispatch_pending_tasks(new_session)


async def _load_stale_tasks(
    local_session: AsyncSession,
    cutoff: datetime,
) -> list[Task]:
    """扫描卡死候选任务并加行锁。

    使用 ``SELECT ... FOR UPDATE SKIP LOCKED`` 避免与并发 ``task_progress``
    事件处理器抢锁。

    Args:
        local_session: 异步数据库会话。
        cutoff: 卡死判定时间线（``updated_at`` 早于该值的任务视为候选）。

    Returns:
        加锁后的候选任务列表（可能为空）。
    """
    result = await local_session.execute(
        select(Task)
        .where(
            Task.status == TaskStatus.RUNNING.value,
            Task.updated_at < cutoff,
            Task.assigned_node_id.is_not(None),
        )
        .with_for_update(skip_locked=True)
    )
    return list(result.scalars().all())


async def _fail_single_stale_task(
    local_session: AsyncSession,
    task: Task,
    now: datetime,
    timeout_reason: str,
) -> bool:
    """对单个卡死任务执行兜底失败动作。

    状态机二次校验：防御 Agent 在兜底扫描与 UPDATE 之间上报了
    ``task_completed``/``task_failed`` 改变了状态。用 raw Core select 直接
    读取当前最新值，避开 ORM expire 后 lazy load 在 ``SELECT...FOR UPDATE``
    事务外再次加载失败的问题。

    兜底动作（同一事务内）：

    1. ``UPDATE tasks SET status='failed'``（仅当仍为 running，状态机守卫）。
    2. ``UPDATE tasks SET change_summary=...``（仅 NULL 时覆写，保留 Agent 上报内容）。
    3. ``UPDATE agent_nodes SET status='idle', load=GREATEST(load-1, 0)``（clamp 防漂移）。
    4. 记录 ``task_stale_failed`` INFO 日志。

    Args:
        local_session: 异步数据库会话。
        task: 加锁后的候选任务。
        now: 本轮兜底的统一时间戳。
        timeout_reason: 写入 change_summary 的失败原因文本。

    Returns:
        任务被成功兜底返回 ``True``；二次校验未通过返回 ``False``。
    """
    current = await local_session.execute(
        select(
            Task.status,
            Task.change_summary,
            Task.updated_at,
            Task.assigned_node_id,
        ).where(Task.id == task.id)
    )
    row = current.first()
    if row is None:
        return False
    current_status, current_change_summary, current_updated_at, current_node_id = row
    if current_status != TaskStatus.RUNNING.value:
        return False
    if current_node_id is None:
        # 不应发生（筛选条件已加 IS NOT NULL），兜底跳过
        return False

    had_change_summary = current_change_summary is not None
    # 第一步：状态机改 failed（必须成功，状态机守卫）。
    # 第二步：写入 change_summary（仅 NULL 时覆写，保留 Agent 上报内容）。
    await local_session.execute(
        update(Task)
        .where(
            Task.id == task.id,
            Task.status == TaskStatus.RUNNING.value,
        )
        .values(
            status=TaskStatus.FAILED.value,
            updated_at=now,
        )
    )
    if not had_change_summary:
        await local_session.execute(
            update(Task)
            .where(
                Task.id == task.id,
                Task.change_summary.is_(None),
            )
            .values(change_summary=timeout_reason)
        )
    # ADR-032：节点状态/负载由心跳派生维护，卡死兜底不再直接改节点行；
    # 在跑槽位随任务置 failed 自然释放（tasks 表统计口径）
    dispatch_backoff.clear_rejections(str(task.id))

    stale_seconds_actual = int((now - (current_updated_at or now)).total_seconds())
    logger.info(
        "task_stale_failed",
        extra={
            "event": "task_stale_failed",
            "task_id": str(task.id),
            "node_id": str(current_node_id),
            "stale_seconds": stale_seconds_actual,
            "had_change_summary": had_change_summary,
        },
    )
    return True


async def _run_stale_task_sweep(
    local_session: AsyncSession,
    stale_seconds: int,
) -> int:
    """执行一轮卡死任务扫描与兜底（不负责 commit/rollback）。

    Args:
        local_session: 异步数据库会话。
        stale_seconds: 卡死判定阈值（秒），由 Pydantic Settings 注入。

    Returns:
        本次兜底失败的任务数量。
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    stale_tasks = await _load_stale_tasks(local_session, cutoff)
    if not stale_tasks:
        return 0

    now = datetime.now(UTC)
    timeout_reason = _STALE_FAIL_REASON_TEMPLATE.format(timeout=stale_seconds)
    failed_count = 0
    for task in stale_tasks:
        if await _fail_single_stale_task(local_session, task, now, timeout_reason):
            failed_count += 1
    return failed_count


async def _fail_stale_tasks(
    stale_seconds: int,
    *,
    session: AsyncSession | None = None,
) -> int:
    """任务卡死兜底：扫描并失败所有「running 且无进度」超时任务。

    判定条件（见 ADR-015 / spec/task-stale-detector-spec.md）：

    - ``status = 'running'``
    - ``updated_at < NOW() - :stale_seconds``
    - ``assigned_node_id IS NOT NULL``

    Args:
        stale_seconds: 卡死判定阈值（秒），由 Pydantic Settings 注入。
        session: 测试场景可注入已存在的会话；缺省时使用
            :data:`AsyncSessionLocal` 新建会话。注入模式由调用方负责
            commit/rollback（通常用于外层 savepoint 包裹的单元测试）。

    Returns:
        本次兜底失败的任务数量。
    """
    if session is not None:
        # 注入模式：调用方负责事务边界（通常为外层 savepoint）
        return await _run_stale_task_sweep(session, stale_seconds)

    async with AsyncSessionLocal() as new_session:
        try:
            failed = await _run_stale_task_sweep(new_session, stale_seconds)
            await new_session.commit()
            return failed
        except Exception:
            await new_session.rollback()
            logger.exception(
                "stale_task_detector_failed",
                extra={"event": "stale_task_detector_failed"},
            )
            return 0


#: 验收阻断等待用户决定的时限（秒）：超时兜底置 failed（ADR-061 M4，用户已定 24h）
VERIFY_BLOCKED_TIMEOUT_SECONDS = 24 * 60 * 60


async def enforce_task_budgets(session: AsyncSession | None = None) -> dict[str, int]:
    """执行任务预算闸（ADR-061 M5b，Q9~Q11）。

    扫描执行中任务，按 `tool_call_logs(kind='llm_usage')` 聚合 steps/tokens，与
    「任务级 override 覆盖全局默认」后的预算比较：

    - ≥ `soft_ratio`（默认 80%）：`steer_task` 发**收尾指令**（Q10 不停任务），记 `budget_state='soft_notified'`；
    - ≥ 100%：`cancel_task(reason=预算耗尽…)` 强制停止，记 `budget_state='hard_stopped'`；
    - **幂等**：同一阈值只触发一次（`budget_state` 守卫），避免每轮循环重复发指令。

    Args:
        session: 测试可注入会话（由调用方 commit）；缺省新建会话。

    Returns:
        ``{"soft": 软通知数, "hard": 硬停数}``。
    """
    if session is not None:
        return await _run_budget_sweep(session)

    async with AsyncSessionLocal() as new_session:
        try:
            counts = await _run_budget_sweep(new_session)
            await new_session.commit()
            return counts
        except Exception:
            await new_session.rollback()
            logger.exception(
                "budget_sweep_failed",
                extra={"event": "budget_sweep_failed"},
            )
            return {"soft": 0, "hard": 0}


async def _run_budget_sweep(session: AsyncSession) -> dict[str, int]:
    """预算闸扫描实现（调用方负责事务边界）。"""
    from app.services import budget_policy_service
    from app.services.task_service import TaskService

    policy = await budget_policy_service.load_policy(session)
    result = await session.execute(
        select(Task).where(
            Task.status.in_((TaskStatus.ASSIGNED.value, TaskStatus.RUNNING.value)),
            # 已硬停过的任务不会仍在执行中，这里只用来避免重复处理已通知的软阈值对象
            or_(Task.budget_state.is_(None), Task.budget_state == budget_policy_service.BUDGET_STATE_SOFT),
        )
    )
    tasks = list(result.scalars().all())
    soft = 0
    hard = 0
    service = TaskService()
    for task in tasks:
        usage = await budget_policy_service.usage_for_task(session, str(task.id))
        budget = budget_policy_service.resolve_budget(task.budget, policy)
        state = budget_policy_service.evaluate(usage, budget)
        action = budget_policy_service.needs_action(task, state)
        if action is None:
            continue
        if action == "hard":
            try:
                await service.cancel_task(
                    session,
                    task.id,
                    reason=budget_policy_service.budget_exceeded_reason(state),
                )
            except Exception:
                logger.warning(
                    "budget_hard_stop_failed",
                    extra={"event": "budget_hard_stop_failed", "task_id": str(task.id)},
                    exc_info=True,
                )
                continue
            task.budget_state = budget_policy_service.BUDGET_STATE_HARD
            await session.commit()
            hard += 1
            logger.warning(
                "budget_hard_stopped",
                extra={
                    "event": "budget_hard_stopped",
                    "task_id": str(task.id),
                    "steps": state["steps"],
                    "tokens": state["tokens"],
                    "ratio": state["ratio"],
                },
            )
            continue
        try:
            await service.steer_task(
                session, task.id, budget_policy_service.wrap_up_instruction(state)
            )
        except Exception:
            # 节点离线等场景：本轮不标记，下一轮重试（避免"没送达却记成已通知"）
            logger.warning(
                "budget_soft_notice_failed",
                extra={"event": "budget_soft_notice_failed", "task_id": str(task.id)},
                exc_info=True,
            )
            continue
        task.budget_state = budget_policy_service.BUDGET_STATE_SOFT
        await session.commit()
        soft += 1
        logger.info(
            "budget_soft_notified",
            extra={
                "event": "budget_soft_notified",
                "task_id": str(task.id),
                "steps": state["steps"],
                "tokens": state["tokens"],
                "ratio": state["ratio"],
            },
        )
    return {"soft": soft, "hard": hard}


async def fail_expired_verify_blocked_tasks(
    session: AsyncSession | None = None,
    *,
    timeout_seconds: int = VERIFY_BLOCKED_TIMEOUT_SECONDS,
) -> int:
    """把超过时限仍无人决定的验收阻断任务兜底置 ``failed``（ADR-061 M4）。

    用户已定：红/无法验证时中枢主动找用户决定，**24h 无回应 → failed**（写明原因，
    不留「既非成功也非失败」的悬挂态）。单条 UPDATE 完成（无节点释放需要：
    阻断时节点已不再执行该任务），返回受影响任务数。

    Args:
        session: 测试可注入会话（由调用方负责 commit）；缺省新建会话。
        timeout_seconds: 时限（秒），缺省 24h。
    """
    if session is not None:
        return await _fail_expired_verify_blocked(session, timeout_seconds)

    async with AsyncSessionLocal() as new_session:
        try:
            count = await _fail_expired_verify_blocked(new_session, timeout_seconds)
            await new_session.commit()
            return count
        except Exception:
            await new_session.rollback()
            logger.exception(
                "verify_blocked_sweep_failed",
                extra={"event": "verify_blocked_sweep_failed"},
            )
            return 0


async def _fail_expired_verify_blocked(session: AsyncSession, timeout_seconds: int) -> int:
    """执行阻断超时兜底（内部实现；调用方负责事务边界）。"""
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=timeout_seconds)
    result = await session.execute(
        update(Task)
        .where(
            Task.status == TaskStatus.VERIFY_BLOCKED.value,
            Task.verify_blocked_at.is_not(None),
            Task.verify_blocked_at <= cutoff,
        )
        .values(
            status=TaskStatus.FAILED.value,
            change_summary="自动验收未通过且 24 小时无人决定，已按失败处理（ADR-061 M4）。",
            updated_at=now,
        )
        .returning(Task.id)
    )
    return len(list(result.scalars().all()))


#: 上次链路事件清理时间（进程内状态；随调度器循环每日触发一次）
_last_tool_call_log_purge: datetime | None = None
#: 上次经验整理时间（ADR-061 M6，进程内状态；每日至多一次）
_last_experience_consolidation: datetime | None = None
#: 经验整理间隔（每日一次即可：pending 是低频事件，草稿还要人工确认）
_EXPERIENCE_CONSOLIDATION_INTERVAL = timedelta(days=1)
#: 清理执行间隔
_TOOL_CALL_LOG_PURGE_INTERVAL = timedelta(days=1)


async def _purge_tool_call_logs_if_due() -> None:
    """执行链路事件保留清理（ADR-047）：每日一次删除超期行。

    首轮调度即执行一次（覆盖上次进程运行期间积累的超期数据），
    之后按 24 小时间隔执行；失败仅告警，不影响调度主流程。
    """
    global _last_tool_call_log_purge
    now = datetime.now(UTC)
    if (
        _last_tool_call_log_purge is not None
        and now - _last_tool_call_log_purge < _TOOL_CALL_LOG_PURGE_INTERVAL
    ):
        return
    _last_tool_call_log_purge = now
    try:
        retention_days = get_settings().tool_call_log_retention_days
        deleted = await tool_call_log_service.purge_expired(retention_days)
        if deleted:
            logger.info(
                "tool_call_logs_purged",
                extra={
                    "event": "tool_call_logs_purged",
                    "deleted_rows": deleted,
                    "retention_days": retention_days,
                },
            )
    except Exception:
        logger.warning(
            "tool_call_logs_purge_failed",
            extra={"event": "tool_call_logs_purge_failed"},
            exc_info=True,
        )


async def _consolidate_experiences_if_due() -> None:
    """经验定时整理（ADR-061 M6）：每日至多一次，把 pending 现象整理成**草稿**。

    行为与失败语义：
    - 首轮调度即执行一次，之后按 24 小时间隔；
    - 只写 `status="draft"`——**不生效**（派发前召回只认 resolved），等人工确认；
    - LLM 不可用/解析失败仅告警，**不抛出**（不影响调度主循环）。
    """
    global _last_experience_consolidation
    now = datetime.now(UTC)
    if (
        _last_experience_consolidation is not None
        and now - _last_experience_consolidation < _EXPERIENCE_CONSOLIDATION_INTERVAL
    ):
        return
    _last_experience_consolidation = now
    try:
        from app.agents.experience_consolidator import ExperienceConsolidator

        async with AsyncSessionLocal() as session:
            result = await ExperienceConsolidator().consolidate(session)
        if result["candidates"]:
            logger.info(
                "experience_consolidation_sweep_done",
                extra={"event": "experience_consolidation_sweep_done", **result},
            )
    except Exception:
        logger.warning(
            "experience_consolidation_sweep_failed",
            extra={"event": "experience_consolidation_sweep_failed"},
            exc_info=True,
        )


async def run_scheduler(stop_event: asyncio.Event) -> None:
    """调度器后台任务主循环。

    每 Settings ``scheduler_interval_seconds`` 秒依次执行一次：

    1. 任务派发：扫描并分配 ``pending`` 任务。
    2. 任务卡死兜底：失败 ``running`` 长期无进度的任务（见 ADR-015）。
    3. 链路事件每日清理（ADR-047）与回避记录过期清收（ADR-059）。

    每轮循环重新读取最新配置，调整阈值无需重启。直到 ``stop_event`` 被置位。

    Args:
        stop_event: 用于通知任务停止的异步事件。
    """
    settings = get_settings()
    logger.info(
        "task_scheduler_started",
        extra={
            "event": "task_scheduler_started",
            "interval_seconds": settings.scheduler_interval_seconds,
            "stale_task_timeout_seconds": settings.stale_task_timeout_seconds,
        },
    )
    logger.info(
        "stale_task_detector_started",
        extra={
            "event": "stale_task_detector_started",
            "interval_seconds": settings.scheduler_interval_seconds,
            "threshold_seconds": settings.stale_task_timeout_seconds,
        },
    )
    while not stop_event.is_set():
        try:
            await _dispatch_once()
        except Exception:
            logger.exception(
                "dispatch_pending_tasks_failed",
                extra={"event": "dispatch_pending_tasks_failed"},
            )

        try:
            await _fail_stale_tasks(get_settings().stale_task_timeout_seconds)
        except Exception:
            # _fail_stale_tasks 内部已捕获并 rollback；这里仅兜底
            logger.exception(
                "stale_task_detector_outer_failed",
                extra={"event": "stale_task_detector_outer_failed"},
            )

        # 验收阻断 24h 无回应兜底（ADR-061 M4）：置 failed 并写明原因
        try:
            expired = await fail_expired_verify_blocked_tasks()
            if expired:
                logger.warning(
                    "verify_blocked_tasks_expired",
                    extra={"event": "verify_blocked_tasks_expired", "count": expired},
                )
        except Exception:
            logger.exception(
                "verify_blocked_sweep_failed",
                extra={"event": "verify_blocked_sweep_failed"},
            )

        # 任务预算闸（ADR-061 M5b）：80% 发收尾指令、100% 强制停止（幂等）
        try:
            budget_counts = await enforce_task_budgets()
            if budget_counts["soft"] or budget_counts["hard"]:
                logger.info(
                    "budget_sweep_completed",
                    extra={"event": "budget_sweep_completed", **budget_counts},
                )
        except Exception:
            logger.exception("budget_sweep_outer_failed", extra={"event": "budget_sweep_outer_failed"})

        # 经验定时整理（ADR-061 M6）：每日至多一次，只产出草稿待人工确认
        await _consolidate_experiences_if_due()

        # 执行链路事件保留清理（ADR-047）：每日一次，失败不影响主流程
        await _purge_tool_call_logs_if_due()

        # ADR-059 卫生项：全局清理解除回避的过期条目。
        # 走到终态之外的路径（用户取消、卡死兜底、拒绝后长期 pending）不会调用
        # clear_rejections，条目不回收会随运行时长单调累积——这里统一兜底。
        pruned = dispatch_backoff.prune_expired()
        if pruned:
            logger.info(
                "dispatch_backoff_pruned",
                extra={"event": "dispatch_backoff_pruned", "removed_entries": pruned},
            )

        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=get_settings().scheduler_interval_seconds,
            )
        except TimeoutError:
            continue

    logger.info("task_scheduler_stopped", extra={"event": "task_scheduler_stopped"})


def start_scheduler() -> tuple[asyncio.Event, asyncio.Task[None]]:
    """启动任务调度器后台任务。

    Returns:
        停止事件与后台任务对象，供 lifespan 注册使用。
    """
    stop_event = asyncio.Event()
    task = asyncio.create_task(run_scheduler(stop_event))
    return stop_event, task


async def stop_scheduler(
    stop_event: asyncio.Event,
    task: asyncio.Task[None],
) -> None:
    """优雅停止任务调度器后台任务。

    Args:
        stop_event: 用于通知任务停止的异步事件。
        task: 后台任务对象。
    """
    stop_event.set()
    try:
        await asyncio.wait_for(task, timeout=SCHEDULER_INTERVAL_SECONDS)
    except TimeoutError:
        logger.warning(
            "task_scheduler_stop_timeout",
            extra={
                "event": "task_scheduler_stop_timeout",
                "task": task.get_name(),
            },
        )
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    except asyncio.CancelledError:
        pass
