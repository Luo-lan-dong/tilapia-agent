"""日志查询技能测试（ADR-047 UT-10）。"""

import logging
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from app.core.logging import RingBufferHandler
from app.skills.base import RESULT_EVENT
from app.skills.query_logs import QueryLogsSkill


@pytest.fixture
def ring_with_entries(monkeypatch) -> RingBufferHandler:
    """注入含已知条目的环形缓冲并替换模块级实例。"""
    import app.core.logging as logging_module

    handler = RingBufferHandler(size=50)
    test_logger = logging.getLogger("query_logs_skill_test")
    test_logger.handlers = [handler]
    test_logger.propagate = False
    test_logger.setLevel(logging.DEBUG)
    test_logger.error("skill_execution_failed 技能执行失败")
    test_logger.warning("节点心跳超时 warning-entry")
    test_logger.info("任务创建成功")
    monkeypatch.setattr(logging_module, "_ring_buffer", handler)
    yield handler
    test_logger.handlers.clear()


async def _collect(
    gen: AsyncGenerator[dict[str, Any], None],
) -> tuple[Any, list[dict[str, Any]]]:
    """消费技能事件流，返回 (_result data, 其他事件)。"""
    result = None
    others = []
    async for event in gen:
        if event.get("event") == RESULT_EVENT:
            result = event.get("data")
        else:
            others.append(event)
    assert result is not None, "技能必须产出 _result 事件"
    return result, others


async def test_query_logs_default_levels(ring_with_entries) -> None:
    """默认级别 error+warning：info 不命中，摘要含计数。"""
    skill = QueryLogsSkill()
    result, others = await _collect(
        skill.execute(None, type("Ctx", (), {"args": {}})())  # type: ignore[arg-type]
    )
    assert others == []
    assert result.success
    assert result.data["count"] == 2
    assert "skill_execution_failed" in result.summary
    assert "任务创建成功" not in result.summary


async def test_query_logs_keyword(ring_with_entries) -> None:
    """关键字过滤命中单条。"""
    skill = QueryLogsSkill()
    result, _ = await _collect(
        skill.execute(None, type("Ctx", (), {"args": {"keyword": "心跳"}})())  # type: ignore[arg-type]
    )
    assert result.data["count"] == 1
    assert "warning-entry" in result.summary


async def test_query_logs_level_param(ring_with_entries) -> None:
    """级别参数生效：level=info 命中 info 条目。"""
    skill = QueryLogsSkill()
    result, _ = await _collect(
        skill.execute(None, type("Ctx", (), {"args": {"level": ["info"]}})())  # type: ignore[arg-type]
    )
    assert result.data["count"] == 1
    assert "任务创建成功" in result.summary


async def test_query_logs_empty_result_hint(ring_with_entries) -> None:
    """无命中时返回引导性提示而非失败。"""
    skill = QueryLogsSkill()
    result, _ = await _collect(
        skill.execute(
            None,
            type("Ctx", (), {"args": {"keyword": "绝对不存在的关键字xyz"}})(),  # type: ignore[arg-type]
        )
    )
    assert result.success
    assert result.data["count"] == 0
    assert "未找到" in result.summary


async def test_query_logs_invalid_args_fallback(ring_with_entries) -> None:
    """非法参数类型回退默认值。"""
    skill = QueryLogsSkill()
    result, _ = await _collect(
        skill.execute(
            None,
            type("Ctx", (), {"args": {"minutes_back": "abc", "limit": "xyz"}})(),  # type: ignore[arg-type]
        )
    )
    assert result.success
    assert result.data["count"] >= 1


async def test_query_logs_limit(ring_with_entries) -> None:
    """limit 参数截断返回条数。"""
    test_logger = logging.getLogger("query_logs_skill_test")
    for index in range(10):
        test_logger.error(f"批量错误 {index}")
    skill = QueryLogsSkill()
    result, _ = await _collect(
        skill.execute(None, type("Ctx", (), {"args": {"limit": 3}})())  # type: ignore[arg-type]
    )
    assert result.data["count"] == 3
