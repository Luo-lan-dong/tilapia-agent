"""经验记忆技能（ADR-050 第三层）：对话中查询经验 / 总结回填。

双通道总结的执行侧：
- 通道 a（收尾提议）：Orchestrator 自然收尾时若有 pending 条目会提示，
  用户同意后 AI 用 experience_recall 查明细 → 提炼草案 → 用户确认 →
  experience_resolve 回填；
- 通道 b（自然语言触发）：用户随时说"把这个坑总结一下"，AI 同链路执行。

结论回填必须经用户确认（ADR-050 已定半自动）：AI 不得跳过确认直接
调用 experience_resolve。
"""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.experience_service import ExperienceService
from app.skills.base import Skill, SkillContext, SkillResult

_EXPERIENCE = ExperienceService()


def _fail(summary: str) -> dict[str, Any]:
    """构造失败结果事件。"""
    return {"event": "_result", "data": SkillResult(success=False, summary=summary)}


def _format_record(record) -> str:
    """经验条目 → 文本摘要（模型可读）。"""
    parts = [
        f"id: {record.id}",
        f"工具: {record.tool or '（通用）'}",
        f"签名: {record.error_signature[:120]}",
        f"状态: {'已总结' if record.status == 'resolved' else '待总结'}"
        f"（命中 {record.hit_count} 次）",
    ]
    if record.problem:
        parts.append(f"现象: {record.problem[:300]}")
    if record.root_cause:
        parts.append(f"根因: {record.root_cause[:300]}")
    if record.solution:
        parts.append(f"解法: {record.solution[:300]}")
    return "- " + "\n  ".join(parts)


class ExperienceRecallSkill(Skill):
    """查询经验记忆（本会话待总结条目 / 关键词搜索）。"""

    name = "experience_recall"
    display_name = "查询经验记忆"
    description = (
        "查询跨会话经验记忆（踩坑结论库）。三种用法：1) scope=session "
        "列出当前会话待总结的踩坑记录；2) scope=all_pending 列出全库所有"
        "待总结条目（含历史会话遗留，跨会话总结时使用）；3) scope=search "
        "按关键词搜索全部经验（排查问题时复用历史结论）。"
        "总结流程：查出待总结条目 → 向用户展示根因/解法草案 → 用户确认后"
        "才调用 experience_resolve 回填（回填不限会话，任何会话都可总结"
        "历史 pending 条目）。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["session", "all_pending", "search"],
                "description": (
                    "session=本会话待总结条目；all_pending=全库待总结条目"
                    "（跨会话）；search=关键词搜索全部经验"
                ),
            },
            "keyword": {
                "type": "string",
                "description": "搜索关键词（scope=search 时使用）",
            },
        },
    }
    returns = "经验条目列表：[{id, tool, 签名, 状态, 命中, 现象, 根因, 解法}]"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """查询经验条目。"""
        scope = str(context.args.get("scope") or "session").strip()
        keyword = str(context.args.get("keyword") or "").strip() or None
        if scope == "search":
            records, total = await _EXPERIENCE.list_records(session, keyword=keyword)
            if not records:
                yield _fail(f"没有匹配「{keyword}」的经验条目。" if keyword else "经验库为空。")
                return
            listing = "\n".join(_format_record(record) for record in records)
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=True,
                    summary=f"共 {total} 条经验（显示 {len(records)} 条）：\n{listing}",
                    data={"total": total},
                ),
            }
            return
        if scope == "all_pending":
            records, total = await _EXPERIENCE.list_records(session, status_filter="pending")
            if not records:
                yield _fail("全库没有待总结的踩坑记录。")
                return
            listing = "\n".join(_format_record(record) for record in records)
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=True,
                    summary=(
                        f"全库共 {total} 条待总结的踩坑记录（含历史会话，回填不限会话）：\n"
                        f"{listing}\n"
                        "请逐条提炼「根因 + 解法」草案，向用户展示并等待明确确认后，"
                        "再以 experience_resolve（record_id + root_cause + solution）回填。"
                    ),
                    data={"count": total},
                ),
            }
            return
        records = await _EXPERIENCE.list_session_pending(session, context.session_id)
        if not records:
            yield _fail(
                "当前会话没有待总结的踩坑记录。历史会话的待总结条目可用 "
                "scope=all_pending 查询（总结回填不限会话）；全部经验可用 "
                "scope=search 按关键词检索。"
            )
            return
        listing = "\n".join(_format_record(record) for record in records)
        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=(
                    f"当前会话共 {len(records)} 条待总结的踩坑记录：\n{listing}\n"
                    "请逐条提炼「根因 + 解法」草案，向用户展示并等待明确确认后，"
                    "再以 experience_resolve（record_id + root_cause + solution）回填。"
                ),
                data={"count": len(records)},
            ),
        }


class ExperienceResolveSkill(Skill):
    """回填经验结论（必须先经用户确认）。"""

    name = "experience_resolve"
    display_name = "回填经验结论"
    description = (
        "把已获用户确认的「根因 + 解法」回填到经验条目（status 变为 resolved）。"
        "前置条件：先用 experience_recall 查出待总结条目，把草案展示给用户并"
        "获得明确同意；用户未确认时禁止调用本技能。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "record_id": {
                "type": "string",
                "description": "经验条目 ID（experience_recall 返回）",
            },
            "root_cause": {"type": "string", "description": "根因（用户确认后的表述）"},
            "solution": {"type": "string", "description": "解法（用户确认后的表述）"},
        },
        "required": ["record_id", "root_cause", "solution"],
    }
    returns = "回填结果确认"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """回填经验结论。"""
        record_id = str(context.args.get("record_id") or "").strip()
        root_cause = str(context.args.get("root_cause") or "").strip()
        solution = str(context.args.get("solution") or "").strip()
        if not record_id or not root_cause or not solution:
            yield _fail(
                "参数不完整：必须提供 record_id、root_cause、solution"
                "（先用 experience_recall 查询待总结条目）。"
            )
            return
        record = await _EXPERIENCE.resolve(
            session, record_id, root_cause=root_cause, solution=solution
        )
        if record is None:
            yield _fail(f"经验条目不存在：{record_id}")
            return
        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=(
                    f"经验已回填（id: {record.id}，状态: 已总结，命中 {record.hit_count} 次）。\n"
                    f"根因: {record.root_cause}\n解法: {record.solution}\n"
                    "后续同类失败将自动注入此经验。"
                ),
                data={"record_id": str(record.id), "status": record.status},
            ),
        }
