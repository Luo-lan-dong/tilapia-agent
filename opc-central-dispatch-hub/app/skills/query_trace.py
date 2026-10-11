"""执行链路查询技能（ADR-047）：模型对话中自助溯源中枢编排事件。

数据源=tool_call_logs 表（LLM 调用 / 技能调用 / 上下文压缩 / 插话提升 /
派发决策 / 节点任务生命周期）；返回摘要行 + 提示按 request_id 溯源，
防上下文爆炸。典型场景：用户问「刚才任务为什么卡住 / 哪一步失败了 /
中枢刚才做了什么」。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import tool_call_log_service
from app.skills.base import Skill, SkillContext, SkillResult

#: 单条摘要行最大长度
_SNIPPET_MAX = 220
#: kind 白名单与中文名（与前端 KIND_LABELS 保持一致）
_KIND_LABELS = {
    "llm_call": "LLM调用",
    "skill_call": "技能调用",
    "skill_result": "技能结果",
    "compaction": "上下文压缩",
    "steering": "插话提升",
    "dispatch": "任务派发",
    "agent_task_event": "节点任务事件",
}
_STATUS_LABELS = {"success": "成功", "started": "进行中", "failed": "失败"}


class QueryTraceSkill(Skill):
    """查询中枢执行链路事件（tool_call_logs）。"""

    name = "query_trace"
    display_name = "链路查询"
    description = (
        "查询中枢最近的执行链路事件（LLM 调用、技能调用、上下文压缩、插话提升、"
        "任务派发决策、节点任务生命周期）。当用户询问中枢刚才做了什么、任务派发"
        "或技能调用的过程与耗时、某次对话编排走到哪一步失败时调用本技能；"
        "比 query_logs 更结构化（带任务 ID 与状态）。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "minutes_back": {
                "type": "integer",
                "description": "可选，回溯分钟数（默认 30，上限 1440）",
            },
            "keyword": {
                "type": "string",
                "description": "可选，关键字（对事件名/参数摘要/结果摘要/错误做模糊匹配）",
            },
            "kind": {
                "type": "string",
                "enum": list(_KIND_LABELS),
                "description": "可选，事件类型过滤",
            },
            "limit": {
                "type": "integer",
                "description": "可选，最多返回条数（默认 20，上限 50）",
            },
        },
    }
    returns = (
        "链路事件摘要行（时间/类型/主体/状态/耗时/错误片段/任务与请求 ID）+ 命中计数；或无命中提示"
    )

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """按参数查询 tool_call_logs 并产出摘要结果事件。"""
        minutes_raw = context.args.get("minutes_back")
        try:
            minutes_back = max(1, min(1440, int(minutes_raw))) if minutes_raw else 30
        except (TypeError, ValueError):
            minutes_back = 30

        keyword = str(context.args.get("keyword") or "").strip() or None
        kind_raw = str(context.args.get("kind") or "").strip() or None
        kinds = {kind_raw} if kind_raw in _KIND_LABELS else None
        limit_raw = context.args.get("limit")
        try:
            limit = max(1, min(50, int(limit_raw))) if limit_raw else 20
        except (TypeError, ValueError):
            limit = 20

        start = datetime.now(UTC) - timedelta(minutes=minutes_back)
        rows = await tool_call_log_service.query_events(
            start=start,
            kinds=kinds,
            keyword=keyword,
            limit=limit,
        )

        if not rows:
            hint = (
                f"最近 {minutes_back} 分钟内没有匹配的链路事件"
                f"（类型={kind_raw or '全部'}，关键字={keyword or '无'}）。"
                "可放宽条件重试；注意链路事件只在对话编排/任务派发发生时记录。"
            )
            yield {
                "event": "_result",
                "data": SkillResult(success=True, summary=hint, data={"count": 0}),
            }
            return

        lines = []
        for row in rows:
            created = str(row.created_at or "")[:19]
            kind_label = _KIND_LABELS.get(row.kind, row.kind)
            status_label = _STATUS_LABELS.get(row.status, row.status)
            detail = row.error or row.result_summary or row.args_summary or ""
            detail = detail[:_SNIPPET_MAX] + ("..." if len(detail) > _SNIPPET_MAX else "")
            duration = f"{row.duration_ms}ms" if row.duration_ms is not None else "-"
            scope = f"task={str(row.task_id)[:8]}" if row.task_id else "task=无"
            request_part = f"req={row.request_id}" if row.request_id else "req=无"
            lines.append(
                f"{created} [{kind_label}] {row.name} {status_label} {duration} "
                f"{scope} {request_part} | {detail}"
            )
        summary = (
            f"查询到 {len(rows)} 条链路事件（最近 {minutes_back} 分钟，时间倒序；"
            "同一 req= 表示同一条编排链路，可按其溯源全链）：\n" + "\n".join(lines)
        )
        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=summary,
                data={"count": len(rows)},
            ),
        }
