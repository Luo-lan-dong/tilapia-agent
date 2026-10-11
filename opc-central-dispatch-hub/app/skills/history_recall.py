"""历史回查技能（ADR-041）：压缩后模型按需检索原始对话历史。"""

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_building_session import TaskBuildingSession
from app.skills.base import Skill, SkillContext, SkillResult

_HISTORY_SNIPPET_MAX = 500


class HistoryRecallSkill(Skill):
    """检索当前会话的原始历史消息（含工具轨迹）。

    上下文压缩（ADR-041）后，窗口中只保留压缩点之后的消息；模型需要更早
    对话中的细节（节点名、任务 ID、命令、结论等）时调用本技能，从数据库
    原始记录中取回，对冲多轮压缩的累积失真。原文永不删除。
    """

    name = "history_recall"
    display_name = "历史回查"
    description = (
        "检索当前会话的原始历史消息（含技能调用轨迹）。当上下文中没有你要的"
        "早期细节（比如之前查过的节点、任务、命令或结论），或对话历史显示"
        "存在 conversation-checkpoint 压缩摘要时，调用本技能按关键词回查原文。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "可选，检索关键词（对消息内容做不区分大小写的子串匹配）",
            },
            "role": {
                "type": "string",
                "enum": ["user", "assistant", "tool"],
                "description": "可选，按消息角色过滤",
            },
            "limit": {
                "type": "integer",
                "description": "可选，最多返回条数（默认 10，上限 30）",
            },
        },
    }
    returns = "命中的历史消息列表（序号/角色/内容片段）或未找到提示"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """按会话检索原始历史并产出结果事件。"""
        if not context.session_id:
            yield {
                "event": "_result",
                "data": SkillResult(success=False, summary="当前对话未关联会话，无法回查历史"),
            }
            return
        try:
            session_uuid = uuid.UUID(context.session_id)
        except ValueError:
            yield {
                "event": "_result",
                "data": SkillResult(success=False, summary="会话 ID 无效，无法回查历史"),
            }
            return
        result = await session.get(TaskBuildingSession, session_uuid)
        if result is None or not result.messages:
            yield {
                "event": "_result",
                "data": SkillResult(success=False, summary="会话不存在或历史为空"),
            }
            return

        keyword = str(context.args.get("keyword") or "").strip().lower()
        role_filter = str(context.args.get("role") or "").strip() or None
        limit_raw = context.args.get("limit")
        try:
            limit = max(1, min(30, int(limit_raw))) if limit_raw else 10
        except (TypeError, ValueError):
            limit = 10

        hits: list[str] = []
        for index, message in enumerate(result.messages):
            if role_filter and message.get("role") != role_filter:
                continue
            content = str(message.get("content") or "")
            tool_calls = message.get("tool_calls") or []
            tool_names = ", ".join(
                str(tc.get("function", {}).get("name", "?")) for tc in tool_calls
            )
            haystack = (content + json.dumps(tool_calls, ensure_ascii=False)).lower()
            if keyword and keyword not in haystack:
                continue
            if content:
                snippet = content
            elif tool_names:
                snippet = f"(无正文，调用技能：{tool_names})"
            else:
                snippet = "(无正文)"
            if len(snippet) > _HISTORY_SNIPPET_MAX:
                snippet = snippet[:_HISTORY_SNIPPET_MAX] + "..."
            role_label = message.get("role")
            if tool_names:
                role_label = f"{role_label}→{tool_names}"
            hits.append(f"#{index} [{role_label}]: {snippet}")
            if len(hits) >= limit:
                break

        if not hits:
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=True,
                    summary="未找到匹配的历史消息，可换关键词或去掉过滤条件重试",
                ),
            }
            return
        summary = f"回查到 {len(hits)} 条历史消息（序号为会话内位置）：\n" + "\n".join(hits)
        yield {
            "event": "_result",
            "data": SkillResult(success=True, summary=summary, data={"count": len(hits)}),
        }
