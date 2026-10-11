"""日志查询技能（ADR-047）：模型对话中自助查询中枢运行日志。

数据源=内存环形缓冲（最近 N 条运行日志快照，重启清空）；返回摘要行 +
命中计数，防上下文爆炸。典型场景：技能调用失败、任务派发异常时，模型
调用本技能查看报错上下文（级别过滤默认 error+warning）。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import read_ring_buffer, ring_buffer_size
from app.skills.base import Skill, SkillContext, SkillResult

#: 单条摘要行最大长度
_SNIPPET_MAX = 300
#: 级别白名单（避免模型传参把 debug/info 全量灌进上下文）
_LEVELS = {"debug", "info", "warning", "error"}
#: 默认查询的级别
_DEFAULT_LEVELS = ("error", "warning")


class QueryLogsSkill(Skill):
    """查询中枢运行日志（内存环形缓冲）。"""

    name = "query_logs"
    display_name = "日志查询"
    description = (
        "查询中枢最近的运行日志（报错与告警）。当上一轮技能调用或任务操作失败、"
        "用户询问刚才发生了什么错误、或你需要报错的具体原因时调用本技能；"
        "可按时间范围（minutes_back）、日志级别与关键字过滤。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "minutes_back": {
                "type": "integer",
                "description": "可选，回溯分钟数（默认 30，上限 1440）",
            },
            "level": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["debug", "info", "warning", "error"],
                },
                "description": "可选，日志级别过滤（默认 error+warning）",
            },
            "keyword": {
                "type": "string",
                "description": "可选，关键字（对消息与结构化字段做不区分大小写的子串匹配）",
            },
            "limit": {
                "type": "integer",
                "description": "可选，最多返回条数（默认 20，上限 50）",
            },
        },
    }
    returns = "命中日志摘要行（时间/级别/来源/消息片段）+ 命中计数；或缓冲为空提示"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """按参数过滤环形缓冲并产出结果事件。"""
        minutes_raw = context.args.get("minutes_back")
        try:
            minutes_back = max(1, min(1440, int(minutes_raw))) if minutes_raw else 30
        except (TypeError, ValueError):
            minutes_back = 30

        levels_raw = context.args.get("level") or list(_DEFAULT_LEVELS)
        if isinstance(levels_raw, str):
            levels_raw = [levels_raw]
        levels = {
            str(level).lower() for level in levels_raw if str(level).lower() in _LEVELS
        } or set(_DEFAULT_LEVELS)

        keyword = str(context.args.get("keyword") or "").strip() or None
        limit_raw = context.args.get("limit")
        try:
            limit = max(1, min(50, int(limit_raw))) if limit_raw else 20
        except (TypeError, ValueError):
            limit = 20

        start = datetime.now(UTC) - timedelta(minutes=minutes_back)
        hits = read_ring_buffer(
            start=start,
            levels=levels,
            keyword=keyword,
            limit=limit,
        )
        total_buffered = ring_buffer_size()

        if not hits:
            hint = (
                f"最近 {minutes_back} 分钟内未找到匹配日志"
                f"（级别={','.join(sorted(levels))}，关键字={keyword or '无'}）。"
                f"当前缓冲共 {total_buffered} 条；可放宽级别/关键字，"
                "或提示用户到日志页（数据源=文件）查询更早记录。"
            )
            yield {
                "event": "_result",
                "data": SkillResult(success=True, summary=hint, data={"count": 0}),
            }
            return

        lines = []
        for hit in hits:
            timestamp = str(hit.get("timestamp") or "")[:19]
            message = str(hit.get("message") or "")
            if len(message) > _SNIPPET_MAX:
                message = message[:_SNIPPET_MAX] + "..."
            lines.append(f"{timestamp} [{hit.get('level')}] {hit.get('logger')}: {message}")
        summary = (
            f"查询到 {len(hits)} 条日志（最近 {minutes_back} 分钟，"
            f"级别={','.join(sorted(levels))}，关键字={keyword or '无'}，"
            f"缓冲共 {total_buffered} 条）：\n" + "\n".join(lines)
        )
        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=summary,
                data={"count": len(hits), "buffer_size": total_buffered},
            ),
        }
