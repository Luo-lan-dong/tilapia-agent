"""智能派发技能：对话中由 AI 将任务派发到最合理节点（ADR-033）。"""

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_node import AgentNode, NodeStatus
from app.models.task import Task, TaskStatus
from app.models.task_building_session import TaskBuildingSession
from app.services import dispatch_backoff
from app.services.dispatch_decision_service import (
    decide_assignments,
    diagnose_eligibility,
    eligibility_gap,
    list_eligible_nodes,
    record_decision,
)
from app.services.task_service import TaskService
from app.skills.base import Skill, SkillContext, SkillResult

logger = logging.getLogger(__name__)


async def _bind_task_to_session(
    session: AsyncSession,
    session_id: str | None,
    task_id: uuid.UUID,
) -> None:
    """派发成功后将任务关联到当前对话会话（失败仅告警，不影响派发结果）。

    前端在每轮对话结束后同步会话详情，发现 task_id 即进入跟踪态
    （右侧产物/任务摘要/方案 Tab 关联该任务）。
    """
    if not session_id:
        return
    try:
        result = await session.execute(
            select(TaskBuildingSession).where(TaskBuildingSession.id == uuid.UUID(session_id))
        )
        chat_session = result.scalar_one_or_none()
        if chat_session is None:
            return
        chat_session.task_id = task_id
        await session.commit()
        logger.info(
            "smart_dispatch_session_task_bound",
            extra={
                "event": "smart_dispatch_session_task_bound",
                "session_id": session_id,
                "task_id": str(task_id),
            },
        )
    except Exception:  # noqa: BLE001 — 会话关联失败不应影响派发结果回传
        logger.warning(
            "smart_dispatch_session_task_bind_failed",
            extra={"event": "smart_dispatch_session_task_bind_failed"},
            exc_info=True,
        )


class SmartDispatchSkill(Skill):
    """将指定任务智能派发到最合理的节点。"""

    name = "smart_dispatch"
    display_name = "智能派发任务"
    description = (
        "将一个待派发（pending）任务派发到最合理的节点：先筛选**可自主**（可用内存与"
        "综合负载双达标）的候选节点，再综合负载与资源选择。用户点名要某台节点时传 "
        "target_node_name（该节点未达标则任务排队等待它，不静默改派他处）；用户明确要求"
        "「不管资源够不够都派过去」时传 force=true（唯一允许越门槛的开关）。"
        "用户要求派发/执行某个任务时调用；如用户未给出任务 ID，先用 get_task_status 查询。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "待派发任务的 UUID"},
            "target_node_name": {
                "type": "string",
                "description": (
                    "可选，用户点名的目标节点名称（如 code / 笔记本）。传入后任务**只等**该节点："
                    "节点达标立即派送；未达标则保持排队并回报缺口，不改派其他节点"
                ),
            },
            "force": {
                "type": "boolean",
                "description": (
                    "可选，默认 false。仅在用户**明确要求**无视资源水位强行派送时传 true；"
                    "该操作会落审计 forced_dispatch=true。不得自行推断用户意图后置真"
                ),
            },
            "preference": {
                "type": "string",
                "description": "可选，用户对节点的偏好描述（如避开某节点），写入决策理由",
            },
        },
        "required": ["task_id"],
    }
    returns = "派发结果：目标节点名称与选择理由；失败时给出原因"

    async def _find_node_by_name(
        self,
        session: AsyncSession,
        name: str,
        agent_type: str,
    ) -> AgentNode | None:
        """按名称精确匹配（不区分大小写）查找同类型节点。"""
        result = await session.execute(
            select(AgentNode).where(
                AgentNode.agent_type == agent_type,
                func.lower(AgentNode.name) == name.lower(),
            )
        )
        return result.scalars().first()

    async def _dispatch_to_named_node(
        self,
        session: AsyncSession,
        task: Task,
        task_id_raw: str,
        target_node_name: str,
        force: bool,
        preference: str,
        session_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """用户点名节点：偏好**决定**选择（ADR-059 D2）。

        节点达标或 ``force=True`` → 直接派送；否则任务排队等待该节点
        （落 ``target_node_id`` 与排队原因），调度器每 10 秒复检，达标即派，
        不经 AI、不改派他处。
        """
        node = await self._find_node_by_name(session, target_node_name, task.agent_type)
        if node is None:
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary=(
                        f"未找到名为「{target_node_name}」的 {task.agent_type} 节点，"
                        "请用 list_nodes 确认节点名称"
                    ),
                ),
            }
            return
        if node.status not in (NodeStatus.IDLE.value, NodeStatus.BUSY.value):
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary=f"节点「{node.name}」当前不在线（{node.status}），无法派发",
                ),
            }
            return

        gap = eligibility_gap(node)
        if gap is not None and not force:
            # 排队等待，不静默改派：用户指定即承诺
            await TaskService().set_dispatch_preference(
                session, task.id, target_node_id=node.id, force=False
            )
            logger.info(
                "smart_dispatch_queued_on_target",
                extra={
                    "event": "smart_dispatch_queued_on_target",
                    "task_id": task_id_raw,
                    "target_node_id": str(node.id),
                    "gap": gap,
                },
            )
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary=(
                        f"节点「{node.name}」暂不可自主：{gap}。任务已排队等待该节点，"
                        "达标后 10 秒内自动派送；如你要求无视资源强行派送，请明确说明"
                    ),
                    data={"node_name": node.name, "queued": True, "gap": gap},
                ),
            }
            return

        yield {
            "event": "status",
            "data": {"message": f"正在按指定派发到节点「{node.name}」…"},
        }
        reason_suffix = f"（用户偏好：{preference}）" if preference else ""
        try:
            result = await TaskService().dispatch_task(
                session,
                task.id,
                target=node.name,
                node_id=node.id,
                force=force,
            )
        except Exception as exc:  # noqa: BLE001 — 派发失败如实回传给 LLM 处理
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary=f"派发失败：{exc}",
                    data={"node": node.name},
                ),
            }
            return
        await _bind_task_to_session(session, session_id, task.id)
        forced_note = "（已按你的要求越过准入门槛强行派送）" if force else ""
        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=(
                    f"已将任务「{task.title}」按指定派发到节点 {node.name}"
                    f"{forced_note}{reason_suffix}。"
                ),
                data={
                    "task_id": task_id_raw,
                    "node_id": str(node.id),
                    "node_name": node.name,
                    "decision_source": "user_preference",
                    "reason": "用户点名指定节点",
                    "forced_dispatch": bool(force),
                    "status": result.get("status"),
                },
            ),
        }

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """执行候选筛选 + AI 决策 + 派发并产出结果事件。"""
        task_id_raw = str(context.args.get("task_id") or "").strip()
        preference = str(context.args.get("preference") or "").strip()
        target_node_name = str(context.args.get("target_node_name") or "").strip()
        force = bool(context.args.get("force", False))
        try:
            task_id = uuid.UUID(task_id_raw)
        except ValueError:
            yield {
                "event": "_result",
                "data": SkillResult(success=False, summary=f"任务 ID 无效：{task_id_raw!r}"),
            }
            return

        task = await session.get(Task, task_id)
        if task is None:
            yield {
                "event": "_result",
                "data": SkillResult(success=False, summary=f"任务 {task_id_raw} 不存在"),
            }
            return
        # 并发防重前置（2026-10-03 用户实测）：决策前回库刷新状态——skill 读到
        # pending 后要跑节点决策（秒级），期间调度器可能已完成自动派发；不刷新
        # 会沿 identity map 旧快照放行，与调度器各派一次（节点两轮执行互踩）。
        await session.refresh(task)
        # 执行中任务重复派发（2026-10-06 用户实测）：编排 Agent 曾在任务已
        # running 后再次派发撞状态校验报错。对 assigned/running 返回**成功
        # 语义**的友好提示，引导 LLM 告知用户任务已在执行、直接跟踪即可。
        if task.status in (TaskStatus.ASSIGNED.value, TaskStatus.RUNNING.value):
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=True,
                    summary=(
                        f"任务「{task.title}」已在节点执行中（状态 {task.status}），"
                        "无需重复派发；请向用户说明任务正在进行，"
                        "可用 get_task_status 跟踪进度。"
                    ),
                    data={
                        "task_id": task_id_raw,
                        "status": task.status,
                        "dispatch_skipped": True,
                    },
                ),
            }
            return
        # pending=正常派发；failed=失败重派（ADR-017：is_redispatch 分支
        # 回 pending 并恢复自动派发，节点会话 ID 固定派生自任务，重新执行
        # 自动 resume 同一 tilacode 会话）——此前仅放行 pending，AI 对失败
        # 任务只能误导用户「重新创建任务」（2026-10-02 用户实测）。
        if task.status not in (TaskStatus.PENDING.value, TaskStatus.FAILED.value):
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary=(
                        f"任务「{task.title}」当前状态为 {task.status}，"
                        "仅 pending / failed 状态可派发"
                        "（verify_blocked 请走验收决策，running/assigned 无需重复派发）"
                    ),
                ),
            }
            return

        # ADR-059 D2：用户点名节点 → 偏好决定选择，不再「记录偏好但照样选最优负载」
        if target_node_name:
            async for item in self._dispatch_to_named_node(
                session,
                task,
                task_id_raw,
                target_node_name,
                force,
                preference,
                context.session_id,
            ):
                yield item
            return

        excluded = dispatch_backoff.excluded_nodes(task_id_raw)
        candidates = await list_eligible_nodes(session, task.agent_type, excluded)
        if not candidates and force:
            # ADR-059 例外路径修复：用户明确要求强行派送时，无「可自主」候选
            # 不应直接保持排队——从该类型全部在线节点选负载最低的一台越过
            # 门槛派发（此前 force 在未指定节点时被忽略，用户必须再手动点名）
            online = [
                item
                for item in await diagnose_eligibility(session, task.agent_type, excluded)
                if item.get("online")
            ]
            if online:
                target = min(online, key=lambda item: item.get("load_score") or 0)
                node = await session.get(AgentNode, uuid.UUID(str(target["node_id"])))
                if node is not None:
                    forced_preference = preference or "用户明确要求强制派送（越过准入门槛）"
                    async for item in self._dispatch_to_named_node(
                        session,
                        task,
                        task_id_raw,
                        node.name,
                        True,
                        forced_preference,
                        context.session_id,
                    ):
                        yield item
                    return
        if not candidates:
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary=(
                        f"任务「{task.title}」当前没有**可自主**节点"
                        "（均离线、可用内存不足 1GB、综合负载 ≥80 或槽位已满）。"
                        "已保持排队；如需强制派送请明确说明，或指定某台节点等待"
                    ),
                ),
            }
            return

        yield {
            "event": "status",
            "data": {"message": f"正在为「{task.title}」从 {len(candidates)} 个候选节点中决策…"},
        }
        decisions = await decide_assignments(session, {task_id_raw: candidates})
        decision = decisions[task_id_raw]
        node = decision.final_node
        assert node is not None  # decide_assignments 保证有候选时 final_node 非空

        # 决策审计随派发事务落库
        session.add(record_decision(decision, batch_id=uuid.uuid4()))
        await session.commit()

        reason_suffix = f"；用户偏好：{preference}" if preference else ""
        try:
            await TaskService().dispatch_task(
                session,
                task_id,
                target=node.name,
                node_id=node.id,
                force=force,
            )
        except Exception as exc:  # noqa: BLE001 — 派发失败如实回传给 LLM 处理
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary=f"派发失败：{exc}",
                    data={"node": node.name},
                ),
            }
            return

        # 派发成功：任务回写当前对话会话（前端据此进入跟踪态，
        # 右侧「产物」/「任务摘要」/「方案」Tab 立即关联该任务）
        await _bind_task_to_session(session, context.session_id, task_id)

        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=(
                    f"已将任务「{task.title}」派发到节点 {node.name}。"
                    f"选择理由：{decision.reason}{reason_suffix}"
                ),
                data={
                    "task_id": task_id_raw,
                    "node_id": str(node.id),
                    "node_name": node.name,
                    "decision_source": decision.decision_source,
                    "reason": decision.reason,
                },
            ),
        }
