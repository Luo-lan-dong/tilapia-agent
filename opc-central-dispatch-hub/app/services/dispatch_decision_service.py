"""AI 智能调度决策服务（ADR-033）。

职责：

1. 候选预筛泛化（``list_available_nodes``）：与调度器规则完全同口径
   （在线 + 资源水位 + 并发槽位 + 回避集合），供 AI 决策、
   ``find_idle_nodes`` 技能共用；
2. AI 批量决策（``decide_assignments``）：一次 LLM 调用为多个多候选任务
   分配节点；输出仅允许候选白名单内节点，非法/超时/异常一律回退规则且
   不重试（幻觉无害化，见 ADR-033 决策 4）；
3. 决策审计（``record_decision``）：每条 AI 决策/快速路径落
   ``dispatch_decisions`` 表。

决策结果仅产生内存中的 :class:`NodeDecision` 与待落库 ORM 对象，
事务提交与 WS 下发由调用方（调度循环）统一负责。
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.agent_node import AgentNode, NodeStatus
from app.models.dispatch_decision import DispatchDecision
from app.models.system_settings import SystemSettings
from app.models.task import Task
from app.services.llm_provider_service import LLMProviderService

logger = logging.getLogger(__name__)

_DECISION_SYSTEM_PROMPT = (
    "你是任务调度助手。系统已按硬性约束（在线状态、准入双门槛、并发槽位）"
    "筛选出每个任务的合格候选节点，你的职责是在候选白名单内为每个任务选择"
    "最合理的节点，并给出简短中文理由。综合考量：综合负载分（越低越空闲）、"
    "资源余量、同一批任务尽量分散到不同节点避免扎堆。"
    "候选均附带 available_memory_gb/load_score/eligible 字段；"
    "**优先选择 eligible=true 的节点**，仅当无 eligible 候选时才可考虑其余候选。"
    "必须且只能从每个任务提供的候选 node_id 中选择，禁止编造节点。"
    '输出严格 JSON：{"decisions": [{"task_id": "...", "node_id": "...", '
    '"reason": "..."}]}，每个任务恰好一条记录。'
)


@dataclass
class NodeDecision:
    """单个任务的派发决策结果。"""

    task_id: str
    node: AgentNode | None = None
    ai_choice_node_id: uuid.UUID | None = None
    validated: bool = False
    fallback: bool = False
    final_node: AgentNode | None = None
    reason: str = ""
    decision_source: str = "rule"  # ai / rule
    duration_ms: int | None = None
    ai_raw_output: str | None = None
    candidates_snapshot: list[dict[str, Any]] = field(default_factory=list)


async def list_available_nodes(
    session: AsyncSession,
    agent_type: str,
    excluded_node_ids: set[str] | None = None,
) -> list[AgentNode]:
    """查询指定类型的全部合格候选节点（ADR-032 预筛逻辑泛化，只读不加锁）。

    候选条件与 ``scheduler._find_best_available_node`` 保持一致：
    在线（idle/busy）+ 心跳资源水位低于阈值（无 metrics 放行）+
    在跑任务数 < 并发槽位上限 + 排除回避集合。按综合负载分升序返回。
    """
    from sqlalchemy import Float, and_, func, or_

    settings = get_settings()
    running_count = (
        select(func.count(Task.id))
        .where(
            Task.assigned_node_id == AgentNode.id,
            Task.status.in_(["assigned", "running"]),
        )
        .correlate(AgentNode)
        .scalar_subquery()
    )
    cpu_percent = AgentNode.metrics_snapshot["cpu"]["percent"].astext.cast(Float)
    mem_percent = AgentNode.metrics_snapshot["memory"]["percent"].astext.cast(Float)
    resource_ok = and_(
        or_(cpu_percent.is_(None), cpu_percent < settings.dispatch_cpu_threshold_percent),
        or_(mem_percent.is_(None), mem_percent < settings.dispatch_mem_threshold_percent),
    )
    conditions: list[Any] = [
        AgentNode.agent_type == agent_type,
        AgentNode.status.in_([NodeStatus.IDLE.value, NodeStatus.BUSY.value]),
        resource_ok,
        running_count < AgentNode.max_concurrent_tasks,
    ]
    if excluded_node_ids:
        conditions.append(AgentNode.id.not_in([uuid.UUID(nid) for nid in excluded_node_ids]))
    result = await session.execute(
        select(AgentNode).where(*conditions).order_by(AgentNode.load.asc())
    )
    return list(result.scalars().all())


async def list_eligible_nodes(
    session: AsyncSession,
    agent_type: str,
    excluded_node_ids: set[str] | None = None,
) -> list[AgentNode]:
    """在合格候选中筛出**可自主派发**者（ADR-059 D1 双门槛，按负载升序）。

    这是 AI 决策的候选域：AI 只能在「可自主」节点内选择，从根本上保证
    「调度评分不越过 AI，AI 也不越过准入门槛」。
    """
    candidates = await list_available_nodes(session, agent_type, excluded_node_ids)
    return [node for node in candidates if is_node_eligible(node)]


async def running_task_counts(session: AsyncSession, node_ids: list[uuid.UUID]) -> dict[str, int]:
    """批量查询各节点在跑任务数（``assigned``/``running``，ADR-032 槽位口径）。"""
    if not node_ids:
        return {}
    from sqlalchemy import func

    result = await session.execute(
        select(Task.assigned_node_id, func.count(Task.id))
        .where(
            Task.assigned_node_id.in_(node_ids),
            Task.status.in_(["assigned", "running"]),
        )
        .group_by(Task.assigned_node_id)
    )
    return {str(node_id): int(count) for node_id, count in result.all()}


async def diagnose_eligibility(
    session: AsyncSession,
    agent_type: str,
    excluded_node_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """诊断指定类型全部**在线**节点的准入状态（ADR-059 D3，排队可视化用）。

    与 ``list_available_nodes`` 的差别：这里**不做资源预筛**，把不合格的
    节点及其缺口一并返回，供前端/对话展示「在等哪台、缺什么」。槽位已满的
    节点标 ``slots_full``。

    Returns:
        节点诊断字典列表（按负载升序）：
        ``{node_id, name, online, available_memory_gb, load_score,
        slots_free, eligible, blocking}``。
    """
    result = await session.execute(select(AgentNode).where(AgentNode.agent_type == agent_type))
    nodes = list(result.scalars().all())
    counts = await running_task_counts(session, [node.id for node in nodes])
    excluded = {str(nid) for nid in (excluded_node_ids or set())}

    diagnostics: list[dict[str, Any]] = []
    for node in nodes:
        node_id = str(node.id)
        used = counts.get(node_id, 0)
        slots_free = max(int(node.max_concurrent_tasks or 0) - used, 0)
        online = node.status in (NodeStatus.IDLE.value, NodeStatus.BUSY.value)
        reasons = blocking_reasons(node)
        blocking = reasons[0] if reasons else None
        if blocking is None and slots_free <= 0:
            blocking = f"并发槽位已满（{used}/{node.max_concurrent_tasks}）"
            reasons = [blocking]
        if blocking is None and node_id in excluded:
            blocking = "该节点刚拒单，回避中（5 分钟内不再派往）"
            reasons = [blocking]
        if blocking is None and not online:
            blocking = "节点不在线"
            reasons = [blocking]
        eligible = online and blocking is None
        diagnostics.append(
            {
                "node_id": node_id,
                "name": node.name,
                "online": online,
                "available_memory_gb": node_available_memory_gb(node),
                "load_score": node_load_score(node),
                "slots_free": slots_free,
                "eligible": eligible,
                "blocking": None if eligible else blocking,
                # ADR-059 卫生项：并列给出全部缺口，避免挤牙膏式反馈
                "blocking_reasons": [] if eligible else reasons,
            }
        )
    diagnostics.sort(key=lambda item: (not item["eligible"], item["load_score"] or 999.0))
    return diagnostics


def build_hold_reason(
    diagnostics: list[dict[str, Any]],
    *,
    target_node_id: str | None = None,
    target_offline: bool = False,
) -> str:
    """构造排队原因文本（ADR-059 D3，含缺口数值）。

    Args:
        diagnostics: ``diagnose_eligibility`` 的输出。
        target_node_id: 若任务已指定目标节点，则原因聚焦该节点。
        target_offline: 指定节点是否离线。

    Returns:
        截断至 200 字符的排队原因（对齐 ``tasks.dispatch_hold_reason`` 列宽）。
    """
    if not diagnostics:
        return "无匹配该类型的 Agent 节点在线"
    if target_node_id:
        node = next((d for d in diagnostics if d["node_id"] == target_node_id), None)
        if node is None:
            return "指定节点已不存在（可能已被移除），请改派或取消指定"
        if target_offline or not node["online"]:
            return f"指定节点「{node['name']}」离线，等待恢复或改派"
        if node["blocking"]:
            return f"指定节点「{node['name']}」暂不可自主：{node['blocking']}"
        return f"指定节点「{node['name']}」暂不可用"
    blocked = [d for d in diagnostics if not d["eligible"]]
    # 同一节点的多个缺口并列（用「/」连接），最多列 3 台，避免超出列宽
    parts = [
        f"{d['name']}（{' / '.join(d.get('blocking_reasons') or [d['blocking'] or '不可用'])}）"
        for d in blocked[:3]
    ]
    return ("无可自主派发的节点：" + "；".join(parts))[:200]


def pick_lowest_load_eligible(diagnostics: list[dict[str, Any]]) -> str | None:
    """在诊断结果中选可自主且负载最低的节点 ID（规则回退用）。"""
    eligible = [d for d in diagnostics if d["eligible"]]
    if not eligible:
        return None
    return min(eligible, key=lambda d: d["load_score"] if d["load_score"] is not None else 999.0)[
        "node_id"
    ]


def build_candidate_snapshot(nodes: list[AgentNode]) -> list[dict[str, Any]]:
    """构造候选节点关键字段快照（上下文瘦身：不含全量 metrics）。"""
    return [
        {
            "node_id": str(node.id),
            "name": node.name,
            "agent_type": node.agent_type,
            "load": node.load,
            "cpu_percent": (node.metrics_snapshot or {}).get("cpu", {}).get("percent"),
            "memory_percent": (node.metrics_snapshot or {}).get("memory", {}).get("percent"),
            # ADR-059 D1：准入判据字段，AI 决策亦可见（避免选到不可自主节点）
            "available_memory_gb": node_available_memory_gb(node),
            "load_score": node_load_score(node),
            "eligible": is_node_eligible(node),
        }
        for node in nodes
    ]


# ---------------- 准入自评（ADR-059 D1）----------------


def node_load_score(node: AgentNode) -> float | None:
    """读取节点心跳 metrics 中的综合负载分（缺失返回 None）。"""
    value = (node.metrics_snapshot or {}).get("load_score", {}).get("score")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def node_available_memory_gb(node: AgentNode) -> float | None:
    """读取节点心跳 metrics 中的**绝对可用内存**（GB，缺失返回 None）。

    ADR-059 D1：主判据用绝对可用量而非百分比——百分比分母随机器规格
    变化，4G 机器 82.7%（仅剩 ~0.7G）会被百分比口径误判为「合规」。
    """
    value = (node.metrics_snapshot or {}).get("memory", {}).get("available_gb")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def node_cpu_percent(node: AgentNode) -> float | None:
    """读取节点心跳 metrics 中的 CPU 使用率（缺失返回 None）。

    仅用于 `load_score` 缺失时的**口径降级兜底**，与节点侧
    `hub_client._resource_busy` 的 CPU 回退保持一致。
    """
    value = (node.metrics_snapshot or {}).get("cpu", {}).get("percent")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def eligibility_gap(node: AgentNode) -> str | None:
    """返回节点不满足准入的**首要缺口描述**；可自主返回 None。

    顺序与 `is_node_eligible` 的短路顺序一致，便于前端展示与日志对齐。
    """
    settings = get_settings()
    available_gb = node_available_memory_gb(node)
    load_score = node_load_score(node)
    if available_gb is not None and available_gb <= settings.dispatch_min_available_memory_gb:
        return f"可用内存 {available_gb:.1f}GB ≤ {settings.dispatch_min_available_memory_gb:.1f}GB"
    if load_score is not None and load_score >= settings.dispatch_max_load_score:
        return f"综合负载 {load_score:.1f} ≥ {settings.dispatch_max_load_score:.1f}"
    if load_score is None:
        cpu_percent = node_cpu_percent(node)
        if cpu_percent is not None and cpu_percent >= settings.dispatch_cpu_threshold_percent:
            return (
                f"负载分缺失，按 CPU 兜底 {cpu_percent:.1f}% ≥ "
                f"{settings.dispatch_cpu_threshold_percent:.1f}%（口径降级）"
            )
    return None


def blocking_reasons(node: AgentNode) -> list[str]:
    """返回节点**全部**不满足准入的缺口描述（可自主时为空列表）。

    与 :func:`eligibility_gap`（只给首要一条，供紧凑展示）的区别：这里并列
    给出每一项，避免「解决内存后刷新才发现负载也不达标」这种挤牙膏式反馈。
    顺序固定为 内存 → 负载 → CPU 兜底，便于前端稳定展示。
    """
    settings = get_settings()
    reasons: list[str] = []
    available_gb = node_available_memory_gb(node)
    if available_gb is not None and available_gb <= settings.dispatch_min_available_memory_gb:
        reasons.append(
            f"可用内存 {available_gb:.1f}GB ≤ {settings.dispatch_min_available_memory_gb:.1f}GB"
        )
    load_score = node_load_score(node)
    if load_score is not None and load_score >= settings.dispatch_max_load_score:
        reasons.append(f"综合负载 {load_score:.1f} ≥ {settings.dispatch_max_load_score:.1f}")
    if load_score is None:
        cpu_percent = node_cpu_percent(node)
        if cpu_percent is not None and cpu_percent >= settings.dispatch_cpu_threshold_percent:
            reasons.append(
                f"负载分缺失，按 CPU 兜底 {cpu_percent:.1f}% ≥ "
                f"{settings.dispatch_cpu_threshold_percent:.1f}%（口径降级）"
            )
    return reasons


def is_node_eligible(node: AgentNode) -> bool:
    """节点是否「可自主派发」（ADR-059 D1 双门槛，保守判定）。

    判据::

        available_memory_gb > 1.0  AND  load_score < 80

    **任一不满足即不可自主**（用户确认的保守口径）。

    口径降级（与节点侧 `_resource_busy` 对齐，避免「中枢放行、节点拒单」错配）：

    - `load_score` 缺失但 CPU 可读 → 用 CPU 百分比兜底判定；
    - **两个指标都缺失**（旧节点）→ 视为「无法判定」放行，不因缺字段误伤
      （百分比预筛已在 `list_available_nodes` 中生效）。
    """
    settings = get_settings()
    available_gb = node_available_memory_gb(node)
    if available_gb is not None and available_gb <= settings.dispatch_min_available_memory_gb:
        return False
    load_score = node_load_score(node)
    if load_score is not None:
        return load_score < settings.dispatch_max_load_score
    cpu_percent = node_cpu_percent(node)
    if cpu_percent is not None:
        return cpu_percent < settings.dispatch_cpu_threshold_percent
    return True


async def is_ai_dispatch_enabled(session: AsyncSession) -> bool:
    """读取 AI 智能调度开关：system_settings 优先，NULL 回退环境变量初始值。"""
    result = await session.execute(
        select(SystemSettings).order_by(SystemSettings.created_at.asc()).limit(1)
    )
    settings_row = result.scalar_one_or_none()
    if settings_row is not None and settings_row.ai_dispatch_enabled is not None:
        return settings_row.ai_dispatch_enabled
    return get_settings().ai_dispatch_enabled


async def _call_llm_json(
    session: AsyncSession,
    user_prompt: str,
) -> str:
    """调用使用中 LLM 生成决策 JSON（超时预算由调用方 wait_for 控制）。"""
    from app.clients.deepseek import DeepSeekClient

    config = await LLMProviderService().get_active_provider(session)
    client = DeepSeekClient(config, scene="ai_dispatch")
    try:
        return await client.chat_completion_messages(
            [
                {"role": "system", "content": _DECISION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
    finally:
        await client.close()


def _parse_decisions(
    raw_output: str,
    task_candidates: dict[str, list[AgentNode]],
) -> dict[str, tuple[uuid.UUID, str]]:
    """解析并校验 LLM 决策输出，返回 task_id -> (node_id, reason)。

    白名单强校验：node_id 不在该任务候选集内（幻觉）或字段缺失时，
    该任务不产生决策记录（由调用方回退规则），不抛异常、不重试。
    """
    try:
        parsed = json.loads(raw_output)
        decisions = parsed.get("decisions")
        if not isinstance(decisions, list):
            return {}
    except json.JSONDecodeError:
        return {}

    valid: dict[str, tuple[uuid.UUID, str]] = {}
    for item in decisions:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id", ""))
        node_id_raw = str(item.get("node_id", ""))
        reason = str(item.get("reason", ""))[:500]
        candidates = task_candidates.get(task_id)
        if not candidates:
            continue
        try:
            node_id = uuid.UUID(node_id_raw)
        except ValueError:
            continue
        if node_id not in {node.id for node in candidates}:
            continue
        valid[task_id] = (node_id, reason)
    return valid


async def decide_assignments(
    session: AsyncSession,
    task_candidates: dict[str, list[AgentNode]],
) -> dict[str, NodeDecision]:
    """批量 AI 决策：一次 LLM 调用为多个多候选任务选择节点。

    Args:
        session: 异步数据库会话（仅读 LLM 配置，不负责提交）。
        task_candidates: 任务 ID -> 合格候选节点列表（按负载升序）。

    Returns:
        task_id -> NodeDecision。LLM 采纳时 decision_source=ai、validated=True；
        幻觉/解析失败/超时/异常时该任务 fallback=True（final_node 为候选中
        负载最低者），不重试。
    """
    started = time.monotonic()
    task_meta = {
        task_id: {"candidates": build_candidate_snapshot(nodes)}
        for task_id, nodes in task_candidates.items()
    }
    user_prompt = "请为以下任务分配节点：\n"
    for task_id, meta in task_meta.items():
        user_prompt += f"\n任务 task_id={task_id}\n候选节点: {json.dumps(meta['candidates'], ensure_ascii=False)}\n"

    raw_output: str | None = None
    error_reason = ""
    try:
        raw_output = await asyncio.wait_for(
            _call_llm_json(session, user_prompt),
            timeout=get_settings().ai_dispatch_timeout_seconds,
        )
    except TimeoutError:
        error_reason = f"AI 决策超时（>{get_settings().ai_dispatch_timeout_seconds}s）"
    except Exception as exc:  # noqa: BLE001 — LLM 任何异常均回退规则
        error_reason = f"AI 决策调用失败：{exc}"
        logger.warning(
            "ai_dispatch_llm_failed",
            extra={"event": "ai_dispatch_llm_failed", "reason": error_reason},
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    parsed = _parse_decisions(raw_output or "", task_candidates) if raw_output else {}

    decisions: dict[str, NodeDecision] = {}
    for task_id, nodes in task_candidates.items():
        decision = NodeDecision(
            task_id=task_id,
            node=nodes[0],
            candidates_snapshot=task_meta[task_id]["candidates"],
            ai_raw_output=raw_output,
            duration_ms=duration_ms,
        )
        if task_id in parsed:
            node_id, reason = parsed[task_id]
            chosen = next(node for node in nodes if node.id == node_id)
            decision.ai_choice_node_id = node_id
            decision.validated = True
            decision.final_node = chosen
            decision.reason = reason or "AI 综合评估选择"
            decision.decision_source = "ai"
        else:
            decision.fallback = True
            decision.final_node = nodes[0]
            decision.reason = error_reason or "AI 输出未通过白名单校验，回退规则（负载最低）"
        decisions[task_id] = decision

    logger.info(
        "ai_dispatch_batch_decided",
        extra={
            "event": "ai_dispatch_batch_decided",
            "task_count": len(task_candidates),
            "ai_adopted": sum(1 for d in decisions.values() if d.validated),
            "fallback": sum(1 for d in decisions.values() if d.fallback),
            "duration_ms": duration_ms,
        },
    )
    return decisions


def record_decision(
    decision: NodeDecision,
    batch_id: uuid.UUID | None = None,
) -> DispatchDecision:
    """将决策结果构造为待落库审计记录（由调用方随主事务提交）。"""
    return DispatchDecision(
        task_id=uuid.UUID(decision.task_id),
        batch_id=batch_id,
        candidates=decision.candidates_snapshot,
        ai_raw_output=(decision.ai_raw_output or "")[:2000] or None,
        ai_choice_node_id=decision.ai_choice_node_id,
        validated=decision.validated,
        fallback=decision.fallback,
        final_node_id=decision.final_node.id if decision.final_node else None,
        reason=(decision.reason or "")[:500] or None,
        decision_source=decision.decision_source,
        duration_ms=decision.duration_ms,
    )
