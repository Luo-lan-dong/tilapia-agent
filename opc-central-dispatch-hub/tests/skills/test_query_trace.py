"""链路查询技能测试（ADR-047 UT-11）。"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.skills.base import RESULT_EVENT
from app.skills.query_trace import QueryTraceSkill


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


def _row(index: int = 0, **overrides: Any) -> SimpleNamespace:
    """构造 query_events 行替身（技能仅读序列化字段）。"""
    base: dict[str, Any] = {
        "created_at": datetime.now(UTC) - timedelta(minutes=index + 1),
        "kind": "llm_call",
        "name": "deepseek-v4-flash",
        "status": "success",
        "duration_ms": 1500 + index,
        "task_id": None,
        "request_id": f"req_row_{index}",
        "session_id": "ses_test",
        "args_summary": None,
        "result_summary": '{"tool_calls": 1}',
        "error": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def patched_query(monkeypatch):
    """替换 tool_call_log_service.query_events 返回固定行集。"""

    def _install(rows: list[SimpleNamespace]):
        async def fake_query_events(**kwargs: Any) -> list[SimpleNamespace]:
            fake_query_events.kwargs = kwargs  # type: ignore[attr-defined]
            return rows

        monkeypatch.setattr(
            "app.skills.query_trace.tool_call_log_service.query_events", fake_query_events
        )
        return fake_query_events

    return _install


async def test_query_trace_summary_lines(patched_query) -> None:
    """命中时输出摘要行：时间/类型中文/状态中文/耗时/req。"""
    patched_query(
        [
            _row(0),
            _row(
                1,
                kind="agent_task_event",
                name="task_failed",
                status="failed",
                error="boom",
                duration_ms=None,
            ),
        ]
    )
    skill = QueryTraceSkill()
    result, others = await _collect(
        skill.execute(None, type("Ctx", (), {"args": {}})())  # type: ignore[arg-type]
    )
    assert others == []
    assert result.success
    assert result.data["count"] == 2
    assert "[LLM调用]" in result.summary
    assert "[节点任务事件]" in result.summary
    assert "失败" in result.summary and "boom" in result.summary
    assert "req_row_0" in result.summary
    assert "1500ms" in result.summary


async def test_query_trace_empty_hint(patched_query) -> None:
    """无命中返回引导提示而非失败。"""
    patched_query([])
    skill = QueryTraceSkill()
    result, _ = await _collect(
        skill.execute(None, type("Ctx", (), {"args": {"keyword": "xyz"}})())  # type: ignore[arg-type]
    )
    assert result.success
    assert result.data["count"] == 0
    assert "没有匹配的链路事件" in result.summary


async def test_query_trace_params_forwarded(patched_query) -> None:
    """minutes/kind/keyword/limit 解析后透传查询。"""
    fake = patched_query([])
    skill = QueryTraceSkill()
    await _collect(
        skill.execute(
            None,
            type(
                "Ctx",
                (),
                {
                    "args": {
                        "minutes_back": 120,
                        "kind": "dispatch",
                        "keyword": "登录页",
                        "limit": 5,
                    }
                },
            )(),  # type: ignore[arg-type]
        )
    )
    kwargs = fake.kwargs  # type: ignore[attr-defined]
    assert kwargs["kinds"] == {"dispatch"}
    assert kwargs["keyword"] == "登录页"
    assert kwargs["limit"] == 5
    assert kwargs["start"] is not None and not kwargs.get("end")


async def test_query_trace_invalid_args_fallback(patched_query) -> None:
    """非法参数回退默认值。"""
    fake = patched_query([])
    skill = QueryTraceSkill()
    await _collect(
        skill.execute(
            None,
            type(
                "Ctx", (), {"args": {"minutes_back": "abc", "limit": "xyz", "kind": "unknown_kind"}}
            )(),  # type: ignore[arg-type]
        )
    )
    kwargs = fake.kwargs  # type: ignore[attr-defined]
    assert kwargs["kinds"] is None  # 非法 kind 不过滤
    assert kwargs["limit"] == 20
