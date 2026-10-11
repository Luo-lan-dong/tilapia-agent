"""经验记忆技能测试（ADR-050）：查询 / 回填 / 收尾总结提示。"""

from __future__ import annotations

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.orchestrator import _experience_pending_hint
from app.models.experience_record import ExperienceRecord
from app.services.experience_service import ExperienceService
from app.skills.base import SkillContext
from app.skills.experience import ExperienceRecallSkill, ExperienceResolveSkill

_SERVICE = ExperienceService()


@pytest.fixture(autouse=True)
async def _clean_records(db_session: AsyncSession):
    """每个用例前后清空 experience_records。"""
    await db_session.execute(delete(ExperienceRecord))
    await db_session.commit()
    yield
    await db_session.execute(delete(ExperienceRecord))
    await db_session.commit()


async def _collect(skill, db_session: AsyncSession, args: dict | None, session_id=None):
    """执行技能并收集事件。"""
    context = SkillContext(args=args or {}, session_id=session_id)
    return [event async for event in skill.execute(db_session, context)]


def _result(events: list[dict]):
    """取技能结果事件。"""
    return events[-1]["data"]


async def _seed_pending(db_session: AsyncSession, session_id: str | None):
    """写入一条待总结条目。"""
    return await _SERVICE.record_failure(
        db_session,
        tool="HtmlProjectInfo",
        error_msg="未找到该网站",
        session_id=session_id,
    )


async def test_recall_session_empty(db_session: AsyncSession) -> None:
    """会话无 pending 条目时如实提示。"""
    result = _result(
        await _collect(ExperienceRecallSkill(), db_session, {"scope": "session"}, "s1")
    )
    assert result.success is False
    assert "待总结" in result.summary


async def test_recall_session_lists_pending(db_session: AsyncSession) -> None:
    """列出本会话 pending 条目并给出总结流程指引。"""
    await _seed_pending(db_session, "s1")
    result = _result(
        await _collect(ExperienceRecallSkill(), db_session, {"scope": "session"}, "s1")
    )
    assert result.success is True
    assert "HtmlProjectInfo" in result.summary
    assert "experience_resolve" in result.summary
    assert "确认" in result.summary


async def test_recall_search(db_session: AsyncSession) -> None:
    """关键词搜索全部经验。"""
    await _seed_pending(db_session, "s1")
    result = _result(
        await _collect(
            ExperienceRecallSkill(),
            db_session,
            {"scope": "search", "keyword": "HtmlProject"},
        )
    )
    assert result.success is True
    assert "未找到该网站" in result.summary


async def test_resolve_backfills_confirmed(db_session: AsyncSession) -> None:
    """回填已确认的根因/解法。"""
    record = await _seed_pending(db_session, "s1")
    result = _result(
        await _collect(
            ExperienceResolveSkill(),
            db_session,
            {
                "record_id": str(record.id),
                "root_cause": "静态站不属于网站模块",
                "solution": "用 Html 族工具查",
            },
        )
    )
    assert result.success is True
    assert record.status == "resolved"
    assert record.root_cause == "静态站不属于网站模块"


async def test_resolve_missing_record(db_session: AsyncSession) -> None:
    """不存在的条目回填失败。"""
    result = _result(
        await _collect(
            ExperienceResolveSkill(),
            db_session,
            {
                "record_id": "00000000-0000-0000-0000-000000000000",
                "root_cause": "r",
                "solution": "s",
            },
        )
    )
    assert result.success is False
    assert "不存在" in result.summary


async def test_resolve_missing_args(db_session: AsyncSession) -> None:
    """缺参返回参数不完整指引。"""
    result = _result(await _collect(ExperienceResolveSkill(), db_session, {}))
    assert result.success is False
    assert "record_id" in result.summary


async def test_pending_hint_empty_without_records(db_session: AsyncSession) -> None:
    """收尾提示：无 pending 条目返回空串。"""
    assert await _experience_pending_hint(db_session, "s1") == ""
    assert await _experience_pending_hint(db_session, None) == ""


async def test_pending_hint_lists_records(db_session: AsyncSession) -> None:
    """收尾提示：有 pending 条目时输出总结引导。"""
    await _seed_pending(db_session, "s1")
    hint = await _experience_pending_hint(db_session, "s1")
    assert "待总结的踩坑经验" in hint
    assert "HtmlProjectInfo" in hint
    assert "总结这些经验" in hint
