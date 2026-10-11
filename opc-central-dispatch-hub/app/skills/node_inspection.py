"""节点查询类技能：查找空闲节点 / 节点资源详情（ADR-033 / ADR-059）。"""

import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_node import AgentNode, NodeStatus
from app.services.dispatch_decision_service import (
    build_candidate_snapshot,
    diagnose_eligibility,
    list_available_nodes,
)
from app.skills.base import Skill, SkillContext, SkillResult


class FindIdleNodesSkill(Skill):
    """查找当前可调度任务执行的空闲节点。"""

    name = "find_idle_nodes"
    display_name = "查找空闲节点"
    description = (
        "查询当前在线且资源充裕（CPU/内存低于阈值、并发槽位未满）的候选节点清单，"
        "按负载从低到高排序。用户询问空闲节点、节点状态、任务应该派到哪时调用。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "agent_type": {
                "type": "string",
                "description": "按节点类型过滤，默认 code（编程 Agent）",
            },
        },
    }
    returns = "候选节点清单：[{node_id, name, agent_type, load, cpu_percent, memory_percent}]"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """执行查询并产出结果事件。"""
        agent_type = str(context.args.get("agent_type") or "code").strip()
        nodes = await list_available_nodes(session, agent_type)
        if nodes:
            lines = [
                f"- {node['name']}（负载 {node['load']}，CPU {node['cpu_percent']}%，"
                f"内存 {node['memory_percent']}%）"
                for node in build_candidate_snapshot(nodes)
            ]
            summary = f"当前共 {len(nodes)} 个可用节点（{agent_type}）：\n" + "\n".join(lines)
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=True, summary=summary, data={"nodes": len(nodes)}
                ),
            }
            return

        # ADR-059 D3 透明化：无「可自主」节点时展示全部在线节点与准入缺口，
        # 而非笼统的「没有可用节点」——用户需要知道「在等哪台、缺什么」
        diagnostics = await diagnose_eligibility(session, agent_type)
        if not diagnostics:
            summary = f"当前没有 {agent_type} 类型的在线节点（该类型可能尚未部署）"
            yield {"event": "_result", "data": SkillResult(success=False, summary=summary)}
            return

        online = [item for item in diagnostics if item.get("online")]
        if not online:
            summary = f"当前没有 {agent_type} 类型的在线节点（共 {len(diagnostics)} 台，全部离线）"
            yield {"event": "_result", "data": SkillResult(success=False, summary=summary)}
            return

        lines = []
        for item in online:
            if item.get("eligible"):
                lines.append(
                    f"- ✅ {item['name']}：可自主派发"
                    f"（可用内存 {item.get('available_memory_gb', 'N/A')}GB，"
                    f"负载分 {item.get('load_score', 'N/A')}）"
                )
            else:
                lines.append(
                    f"- ❌ {item['name']}：在线但未达派发门槛"
                    f"（原因：{item.get('blocking') or '未知'}；"
                    f"可用内存 {item.get('available_memory_gb', 'N/A')}GB，"
                    f"负载分 {item.get('load_score', 'N/A')}）"
                )
        summary = (
            f"{agent_type} 类型共 {len(online)} 台在线，其中 "
            f"{sum(1 for item in online if item.get('eligible'))} 台可自主派发：\n"
            + "\n".join(lines)
            + "\n如需立即派发到未达标节点，可在用户确认后用 smart_dispatch"
            "（target_node_name + force=true）强行派送。"
        )
        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=summary,
                data={"diagnostics": online},
            ),
        }


class GetNodeMetricsSkill(Skill):
    """查询节点实时资源监控详情。"""

    name = "get_node_metrics"
    display_name = "查询节点资源详情"
    description = (
        "查询节点的 CPU/内存/磁盘等实时资源监控数据。不传 node_id 时返回全部"
        "在线节点概要；需要特定节点详情或对比多节点资源时调用。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "node_id": {
                "type": "string",
                "description": "可选，节点 UUID；缺省返回全部在线节点",
            },
        },
    }
    returns = "节点资源快照：{name, status, cpu, memory, disk, load} 或在线节点列表"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """读取心跳 metrics 快照并产出结果事件。"""
        node_id_raw = str(context.args.get("node_id") or "").strip()
        if node_id_raw:
            try:
                node = await session.get(AgentNode, uuid.UUID(node_id_raw))
            except ValueError:
                node = None
            if node is None:
                yield {
                    "event": "_result",
                    "data": SkillResult(success=False, summary=f"节点 {node_id_raw} 不存在"),
                }
                return
            summary = _format_node_metrics(node)
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=True, summary=summary, data={"metrics": node.metrics_snapshot}
                ),
            }
            return

        result = await session.execute(
            select(AgentNode)
            .where(AgentNode.status.in_([NodeStatus.IDLE.value, NodeStatus.BUSY.value]))
            .order_by(AgentNode.load.asc())
        )
        nodes = list(result.scalars().all())
        summary = "\n".join(_format_node_metrics(node) for node in nodes) or "当前无在线节点"
        yield {
            "event": "_result",
            "data": SkillResult(
                success=bool(nodes),
                summary=f"共 {len(nodes)} 个在线节点：\n{summary}",
                data={"count": len(nodes)},
            ),
        }


def _format_node_metrics(node: AgentNode) -> str:
    """将节点 metrics 快照格式化为人类可读文本。"""
    metrics = node.metrics_snapshot or {}
    cpu = metrics.get("cpu", {})
    memory = metrics.get("memory", {})
    return (
        f"{node.name}（{node.status}）：CPU {cpu.get('percent', 'N/A')}%，"
        f"内存 {memory.get('percent', 'N/A')}%，负载分 {node.load}"
    )
