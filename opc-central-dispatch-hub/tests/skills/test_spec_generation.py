"""generate_spec_docs 技能测试（ADR-033）：生成结果回写执行上下文。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.skills.base import SkillContext
from app.skills.spec_generation import GenerateSpecDocsSkill

_GENERATED_SPEC = {
    "spec": "# 任务规范\n\n开发静态登录页面。",
    "tasks": "# 任务清单\n\n1. 编写 login.html",
    "checklist": "# 验收清单\n\n- 页面可打开",
}


class _FakeSpecBuilderAgent:
    """模拟 SpecBuilderAgent：产出 specification 事件流。"""

    def __init__(self, _session: object) -> None:
        pass

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        current_specification: dict[str, Any] | None,
        mode: str = "thinking",
    ) -> AsyncGenerator[dict[str, Any], None]:
        yield {"event": "meta", "data": {"model": "test-model", "mode": mode}}
        yield {"event": "chunk", "data": {"delta": "已生成三件套"}}
        yield {"event": "specification", "data": _GENERATED_SPEC}
        yield {"event": "status", "data": {"is_ready_to_create": True}}
        yield {"event": "done", "data": {}}


async def test_generated_spec_written_back_to_context(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """技能生成三件套后必须回写 context.current_specification——同轮内
    create_task 才能读到（修复：回写缺失导致连续创建失败）。"""
    monkeypatch.setattr("app.skills.spec_generation.SpecBuilderAgent", _FakeSpecBuilderAgent)

    context = SkillContext(messages=[], current_specification=None)
    events = [event async for event in GenerateSpecDocsSkill().execute(db_session, context)]

    result = next(e["data"] for e in events if e["event"] == "_result")
    assert result.success is True
    assert result.data["specification"] == _GENERATED_SPEC
    # 关键断言：上下文回写
    assert context.current_specification == _GENERATED_SPEC
    # specification 全量事件已转发（前端面板更新）
    specification_events = [e for e in events if e["event"] == "specification"]
    assert specification_events and specification_events[0]["data"] == _GENERATED_SPEC


async def test_generation_failure_leaves_context_unchanged(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生成失败（无 specification 事件）时不回写，保持原值。"""
    monkeypatch.setattr("app.skills.spec_generation.SpecBuilderAgent", _FakeSpecBuilderAgent)
    original = {"spec": "旧值", "tasks": "旧值", "checklist": "旧值"}

    class _EmptyAgent(_FakeSpecBuilderAgent):
        async def chat_stream(  # noqa: ANN202 — 测试桩
            self, messages, current_specification, mode: str = "thinking"
        ):
            yield {"event": "meta", "data": {"model": "m", "mode": "fast"}}
            yield {"event": "done", "data": {}}

    monkeypatch.setattr("app.skills.spec_generation.SpecBuilderAgent", _EmptyAgent)

    context = SkillContext(messages=[], current_specification=original)
    events = [event async for event in GenerateSpecDocsSkill().execute(db_session, context)]

    result = next(e["data"] for e in events if e["event"] == "_result")
    assert result.success is False
    assert context.current_specification == original


async def test_generation_failure_forwards_error_and_clear_summary(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生成失败时 error 事件必须转发前端，且 summary 明确"生成失败"——
    防止 LLM 误以为文档已更新而继续 create_task 基于旧草稿误建任务。"""

    class _ErrorAgent(_FakeSpecBuilderAgent):
        async def chat_stream(  # noqa: ANN202 — 测试桩
            self, messages, current_specification, mode: str = "thinking"
        ):
            yield {"event": "meta", "data": {"model": "m", "mode": "fast"}}
            yield {"event": "status", "data": {"message": "正在编写规范文档…"}}
            yield {"event": "error", "data": {"message": "LLM 超时"}}
            yield {"event": "done", "data": {}}

    monkeypatch.setattr("app.skills.spec_generation.SpecBuilderAgent", _ErrorAgent)
    original = {"spec": "旧值", "tasks": "旧值", "checklist": "旧值"}

    context = SkillContext(messages=[], current_specification=original)
    events = [event async for event in GenerateSpecDocsSkill().execute(db_session, context)]

    error_events = [e for e in events if e["event"] == "error"]
    assert error_events and error_events[0]["data"]["message"] == "LLM 超时"
    result = next(e["data"] for e in events if e["event"] == "_result")
    assert result.success is False
    assert "生成失败" in result.summary
    assert "LLM 超时" in result.summary
    # 不得出现误导性的"已更新"话术
    assert "已更新" not in result.summary
    assert context.current_specification == original
