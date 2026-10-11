"""历史回查技能单元测试（ADR-041）。"""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.skills.base import SkillContext, SkillResult
from app.skills.history_recall import HistoryRecallSkill

_RESULT = "_result"


def _context(session_id: str | None, args: dict[str, Any] | None = None) -> SkillContext:
    return SkillContext(session_id=session_id, args=args or {})


async def _run(skill: HistoryRecallSkill, session: Any, context: SkillContext) -> SkillResult:
    result: SkillResult | None = None
    async for event in skill.execute(session, context):
        if event.get("event") == _RESULT:
            result = event["data"]
    assert result is not None
    return result


@pytest.fixture
def session_row() -> SimpleNamespace:
    """模拟会话行（messages 与数据库一致的结构）。"""
    return SimpleNamespace(
        messages=[
            {"role": "user", "content": "帮我看看有哪些空闲节点"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "find_idle_nodes"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "节点 code 负载 5.0"},
            {"role": "assistant", "content": "code 节点最空闲"},
        ]
    )


class _FakeDbSession:
    def __init__(self, row: Any) -> None:
        self._row = row

    async def get(self, model: Any, key: Any) -> Any:
        return self._row


async def test_history_recall_keyword_match(session_row: SimpleNamespace) -> None:
    """UT-14：keyword 不区分大小写子串匹配，返回序号+角色+片段。"""
    skill = HistoryRecallSkill()
    result = await _run(
        skill,
        _FakeDbSession(session_row),
        _context(str(uuid.uuid4()), {"keyword": "CODE"}),
    )
    assert result.success is True
    assert "#2 [tool]" in result.summary
    assert "节点 code 负载 5.0" in result.summary


async def test_history_recall_role_filter_and_limit(session_row: SimpleNamespace) -> None:
    """UT-15：role 过滤 + 无 keyword 返回最近 N 条。"""
    skill = HistoryRecallSkill()
    result = await _run(
        skill,
        _FakeDbSession(session_row),
        _context(str(uuid.uuid4()), {"role": "user", "limit": 1}),
    )
    assert result.success is True
    assert result.summary.count("#") == 1
    assert "[user]" in result.summary


async def test_history_recall_tool_calls_searchable(session_row: SimpleNamespace) -> None:
    """工具轨迹（tool_calls 技能名）可被关键词命中。"""
    skill = HistoryRecallSkill()
    result = await _run(
        skill,
        _FakeDbSession(session_row),
        _context(str(uuid.uuid4()), {"keyword": "find_idle_nodes"}),
    )
    assert result.success is True
    assert "find_idle_nodes" in result.summary


async def test_history_recall_no_session() -> None:
    """UT-16：未关联会话时优雅返回。"""
    skill = HistoryRecallSkill()
    result = await _run(skill, _FakeDbSession(None), _context(None))
    assert result.success is False
    assert "未关联会话" in result.summary


async def test_history_recall_no_match(session_row: SimpleNamespace) -> None:
    """UT-16：无匹配时返回提示。"""
    skill = HistoryRecallSkill()
    result = await _run(
        skill,
        _FakeDbSession(session_row),
        _context(str(uuid.uuid4()), {"keyword": "不存在的关键词"}),
    )
    assert result.success is True
    assert "未找到" in result.summary


async def test_history_recall_db_session_integration(
    db_session: AsyncSession, session_row: SimpleNamespace
) -> None:
    """真实 AsyncSession.get 走库路径（会话行不存在）。"""
    skill = HistoryRecallSkill()
    result = await _run(skill, db_session, _context(str(uuid.uuid4())))
    assert result.success is False
    assert "历史为空" in result.summary
