"""OrchestratorAgent 技能循环测试（ADR-033）：mock LLM 验证事件流。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.orchestrator import OrchestratorAgent
from app.clients.deepseek import DeepSeekAPIError


class FakeDeepSeekClient:
    """模拟 DeepSeekClient：按脚本依次返回 tool_calls / 最终回答（ADR-040 流式）。

    脚本项支持特殊键：
    - ``{"overflow": True}``：抛 context overflow 400（ADR-041 兜底测试用）；
    - ``{"summary": "..."}``：压缩摘要（chat_completion_messages）；
    - ``{"api_error": (status, msg)}``：抛 DeepSeekAPIError（ADR-044 降级测试用）。
    """

    def __init__(
        self,
        script: list[dict[str, Any]],
        usage: dict[str, Any] | None = None,
        context_window: int = 65536,
    ) -> None:
        self._config = SimpleNamespace(
            model="test-model", supports_vision=False, context_window=context_window
        )
        self._script = script
        self._usage = usage
        self._cursor = 0
        self.calls: list[dict[str, Any]] = []

    async def chat_completion_stream_raw(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        reasoning: bool = False,
        thinking: bool | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        response = self._script[min(self._cursor, len(self._script) - 1)]
        self._cursor += 1
        if response.get("overflow"):
            raise DeepSeekAPIError(
                status_code=400,
                message="This model's maximum context length is 65536 tokens",
            )
        if response.get("api_error"):
            status, message = response["api_error"]
            raise DeepSeekAPIError(status_code=status, message=message)
        content = response.get("content") or ""
        # 正文按 5 字符分片模拟真流式增量（验证 chunk 实时性而非整段切块）
        for pos in range(0, len(content), 5):
            yield {"type": "content_delta", "delta": content[pos : pos + 5]}
        tool_calls = response.get("tool_calls")
        message: dict[str, Any] = {"role": "assistant", "content": content or None}
        if tool_calls:
            message["tool_calls"] = tool_calls
        yield {"type": "message", "message": message, "usage": self._usage}

    async def chat_completion_messages(self, messages: list[dict[str, Any]], **_: object) -> str:
        self.calls.append({"messages": messages, "tools": None, "tool_choice": None})
        return "## Objective\n测试压缩摘要"

    async def close(self) -> None:
        """对齐真实客户端接口：chat_stream finally 清理降级 client 时调用。"""


async def _collect(gen: Any) -> list[dict[str, Any]]:
    return [event async for event in gen]


async def test_skill_call_flow_emits_step_events(db_session: AsyncSession) -> None:
    """LLM 调用技能 → step_started/finished → 最终回答 chunk/done。"""
    client = FakeDeepSeekClient(
        [
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "find_idle_nodes",
                            "arguments": json.dumps({"agent_type": "code"}),
                        },
                    }
                ],
            },
            {"content": "当前没有空闲节点", "tool_calls": None},
        ]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(
        agent.chat_stream([{"role": "user", "content": "看下空闲节点"}], mode="fast")
    )

    names = [event["event"] for event in events]
    assert names[0] == "meta"
    assert names[0 + 1 :] and "step_started" in names
    assert "step_finished" in names
    assert names[-1] == "done"
    # chunk 为流式增量分片，拼接后应包含最终回答内容（ADR-040 真流式）
    reply = "".join(e["data"]["delta"] for e in events if e["event"] == "chunk")
    assert "空闲节点" in reply

    started = next(e for e in events if e["event"] == "step_started")["data"]
    assert started["skill_name"] == "find_idle_nodes"
    assert started["seq"] == 1
    finished = next(e for e in events if e["event"] == "step_finished")["data"]
    assert finished["step_id"] == started["step_id"]
    assert finished["status"] in {"success", "failed"}
    assert finished["duration_ms"] is not None
    # 技能结果以 tool role 消息回传
    second_call_messages = client.calls[1]["messages"]
    assert second_call_messages[-1]["role"] == "tool"
    assert second_call_messages[-1]["tool_call_id"] == "call-1"


async def test_unknown_skill_name_handled(db_session: AsyncSession) -> None:
    """LLM 幻觉出未知技能名时：step_failed + 错误结果回传，不崩溃。"""
    client = FakeDeepSeekClient(
        [
            {
                "content": None,
                "tool_calls": [
                    {"id": "call-x", "function": {"name": "no_such_tool", "arguments": "{}"}}
                ],
            },
            {"content": "技能不可用", "tool_calls": None},
        ]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    finished = next(e for e in events if e["event"] == "step_finished")["data"]
    assert finished["status"] == "failed"
    tool_msg = client.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool"
    assert "未知技能" in tool_msg["content"]


async def test_max_tool_rounds_forces_final_answer(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """达到最大工具轮数后强制要求最终回答，不再调用技能。"""

    class _AlwaysToolClient(FakeDeepSeekClient):
        async def chat_completion_stream_raw(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            tool_choice: str | None = None,
            reasoning: bool = False,
            thinking: bool | None = None,
            max_tokens: int | None = None,
        ) -> AsyncGenerator[dict[str, Any], None]:
            self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
            self._cursor += 1
            if tools:
                yield {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": "c", "function": {"name": "get_task_status", "arguments": "{}"}}
                        ],
                    },
                }
                return
            # 强制收敛轮（无 tools）：纯文本收尾
            yield {"type": "content_delta", "delta": "已达到最大轮数"}
            yield {"type": "message", "message": {"role": "assistant", "content": "已达到最大轮数"}}

    monkeypatch.setattr("app.agents.orchestrator.get_settings", _fake_settings(2))
    client = _AlwaysToolClient([])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    # 2 轮工具调用 + 1 轮强制收敛
    assert len(client.calls) == 3
    last_messages = client.calls[-1]["messages"]
    assert any("最大技能调用轮数" in str(m.get("content") or "") for m in last_messages)
    # ADR-040 三重禁用：收尾轮不携带 tools 且 tool_choice="none"
    assert client.calls[-1]["tools"] is None
    assert client.calls[-1]["tool_choice"] == "none"
    assert events[-1]["event"] == "done"


def _fake_settings(rounds: int | None = None, **overrides: Any) -> Any:
    """构造 Settings 代理：orchestrator_max_tool_rounds 与 overrides 覆盖，
    其余透传真实值（ADR-043 起支持 skill 超时 / doom 阈值注入）。"""
    from app.core.config import get_settings as real_get_settings

    class _SettingsProxy:
        def __getattr__(self, name: str) -> Any:
            if name == "orchestrator_max_tool_rounds" and rounds is not None:
                return rounds
            if name in overrides:
                return overrides[name]
            return getattr(real_get_settings(), name)

    return lambda: _SettingsProxy()


async def test_fake_tool_syntax_corrected(db_session: AsyncSession) -> None:
    """模型输出 DSML 伪工具调用文本时：一次修正重试（不带 tools）后输出干净回答。"""

    class _DsmlThenCleanClient(FakeDeepSeekClient):
        async def chat_completion_stream_raw(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            tool_choice: str | None = None,
            reasoning: bool = False,
            thinking: bool | None = None,
            max_tokens: int | None = None,
        ) -> AsyncGenerator[dict[str, Any], None]:
            self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
            self._cursor += 1
            if self._cursor == 1:
                dsml = '<|DSML|><invoke name="find_idle_nodes"></|DSML|>'
                yield {"type": "content_delta", "delta": dsml}
                yield {"type": "message", "message": {"role": "assistant", "content": dsml}}
                return
            clean = "该技能已被禁用，无法调用"
            yield {"type": "content_delta", "delta": clean}
            yield {"type": "message", "message": {"role": "assistant", "content": clean}}

    client = _DsmlThenCleanClient([])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    # 首轮带 tools，修正轮不带 tools（强制文本回答）
    assert len(client.calls) == 2
    assert client.calls[0]["tools"] is not None
    assert client.calls[1]["tools"] is None
    assert any("工具调用标记" in str(m.get("content") or "") for m in client.calls[1]["messages"])
    # 最终回答为修正后的干净文本，不含 DSML 标记
    reply = "".join(e["data"]["delta"] for e in events if e["event"] == "chunk")
    assert "该技能已被禁用" in reply
    assert "DSML" not in reply and "invoke" not in reply
    assert events[-1]["event"] == "done"


async def test_fake_tool_syntax_stripped_when_correction_unavailable(
    db_session: AsyncSession,
) -> None:
    """修正轮不可用（异常）时剥离标记兜底输出，不崩溃。"""

    class _AlwaysDsmlClient(FakeDeepSeekClient):
        async def chat_completion_stream_raw(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            tool_choice: str | None = None,
            reasoning: bool = False,
            thinking: bool | None = None,
            max_tokens: int | None = None,
        ) -> AsyncGenerator[dict[str, Any], None]:
            self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
            raise RuntimeError("provider down")
            yield  # pragma: no cover - 使其成为生成器

    client = _AlwaysDsmlClient([])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    # DeepSeekAPIError 之外的异常走统一 error 兜底，不崩溃
    assert events[-1]["event"] == "done"
    assert any(e["event"] == "error" for e in events)


def test_build_system_prompt_lists_enabled_and_disabled() -> None:
    """系统提示词动态生成：启用/禁用技能清单分离，禁用技能带不可调用说明。"""
    from app.agents.orchestrator_prompts import build_system_prompt

    prompt = build_system_prompt(
        enabled=[("find_idle_nodes", "查询空闲节点")],
        disabled=[("generate_spec_docs", "生成三件套规范文档")],
    )
    assert "find_idle_nodes：查询空闲节点" in prompt
    assert "generate_spec_docs" in prompt
    assert "已禁用" in prompt
    assert "严禁在回答文本中输出任何工具调用语法" in prompt


async def test_tool_call_args_passed_to_skill(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 工具调用参数必须进入技能执行上下文（修复：args 恒为空导致
    smart_dispatch 报「任务 ID 无效」）。"""
    from app.skills.base import Skill, SkillResult

    captured: dict[str, Any] = {}

    class _CaptureSkill(Skill):
        name = "find_idle_nodes"
        display_name = "查找空闲节点"
        description = "capture"
        parameters: dict[str, Any] = {"type": "object", "properties": {}}
        returns = "ok"

        def execute(self, session: AsyncSession, context: Any):
            async def _gen():
                captured["args"] = context.args
                yield {
                    "event": "_result",
                    "data": SkillResult(success=True, summary="ok"),
                }

            return _gen()

    monkeypatch.setattr(
        "app.agents.orchestrator.find_skill",
        lambda key: _CaptureSkill() if key == "find_idle_nodes" else None,
    )

    client = FakeDeepSeekClient(
        [
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-args",
                        "function": {
                            "name": "find_idle_nodes",
                            "arguments": json.dumps({"agent_type": "video"}),
                        },
                    }
                ],
            },
            {"content": "done", "tool_calls": None},
        ]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    assert captured["args"] == {"agent_type": "video"}
    finished = next(e for e in events if e["event"] == "step_finished")["data"]
    assert finished["status"] == "success"


_GENERATED_SPEC = {
    "spec": "# 任务规范\n\n开发静态登录页面。",
    "tasks": "# 任务清单\n\n1. 编写 login.html",
    "checklist": "# 验收清单\n\n- 页面可打开",
}


class _FakeSpecBuilderAgent:
    """模拟 SpecBuilderAgent：产出 specification 事件流（/spec 用）。"""

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


async def test_spec_command_forces_spec_skill(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/spec 精确匹配：确定性执行 generate_spec_docs，LLM 决策轮零调用（ADR-036）。"""
    monkeypatch.setattr("app.skills.spec_generation.SpecBuilderAgent", _FakeSpecBuilderAgent)
    client = FakeDeepSeekClient([])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "/spec"}], mode="fast"))

    # 关键断言：未走 LLM 决策轮
    assert client.calls == []
    names = [e["event"] for e in events]
    assert "step_started" in names and "step_finished" in names
    started = next(e for e in events if e["event"] == "step_started")["data"]
    assert started["skill_name"] == "generate_spec_docs"
    assert any(e["event"] == "specification" and e["data"] == _GENERATED_SPEC for e in events)
    reply = "".join(e["data"]["delta"] for e in events if e["event"] == "chunk")
    assert "已生成三件套" in reply
    assert names[-1] == "done"


async def test_spec_command_tolerates_whitespace(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/spec 前后空白在 strip 后仍精确匹配。"""
    monkeypatch.setattr("app.skills.spec_generation.SpecBuilderAgent", _FakeSpecBuilderAgent)
    client = FakeDeepSeekClient([])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(
        agent.chat_stream([{"role": "user", "content": "  /spec  "}], mode="fast")
    )

    assert client.calls == []
    assert any(e["event"] == "step_started" for e in events)


async def test_normal_message_not_intercepted(db_session: AsyncSession) -> None:
    """普通消息（含非精确 /spec 文本）不拦截，正常走 LLM 决策轮。"""
    client = FakeDeepSeekClient([{"content": "好的，请描述需求", "tool_calls": None}])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(
        agent.chat_stream([{"role": "user", "content": "/spec 帮我生成文档"}], mode="fast")
    )

    assert len(client.calls) == 1
    assert not any(e["event"] == "step_started" for e in events)
    assert events[-1]["event"] == "done"


async def test_spec_command_works_when_skill_disabled(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """技能被禁用时 /spec 仍强制执行（显式命令优先于技能开关）。"""

    async def _none_enabled(_session: object) -> list[Any]:
        return []

    monkeypatch.setattr("app.agents.orchestrator.get_enabled_skills", _none_enabled)
    monkeypatch.setattr("app.skills.spec_generation.SpecBuilderAgent", _FakeSpecBuilderAgent)
    client = FakeDeepSeekClient([])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "/spec"}], mode="fast"))

    assert client.calls == []
    started = next(e for e in events if e["event"] == "step_started")["data"]
    assert started["skill_name"] == "generate_spec_docs"
    assert events[-1]["event"] == "done"


async def test_generate_then_create_task_same_round(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一轮 generate_spec_docs → create_task 串联成功（ADR-038 会话修复回归）。

    generate_spec_docs 技能把三件套写回 SkillContext；Orchestrator 必须将
    更新写回会话流转，否则同轮后续的 create_task 恒报
    「当前对话还没有可执行的三件套规范文档」。
    """

    async def _all_enabled(_session: object) -> list[Any]:
        from app.skills.registry import SKILL_REGISTRY

        return [cls() for cls in SKILL_REGISTRY]

    monkeypatch.setattr("app.agents.orchestrator.get_enabled_skills", _all_enabled)
    monkeypatch.setattr("app.skills.spec_generation.SpecBuilderAgent", _FakeSpecBuilderAgent)

    client = FakeDeepSeekClient(
        [
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-gen",
                        "function": {"name": "generate_spec_docs", "arguments": "{}"},
                    }
                ],
            },
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-create",
                        "function": {
                            "name": "create_task",
                            "arguments": json.dumps({"requirement": "登录页面任务"}),
                        },
                    }
                ],
            },
            {"content": "任务已创建", "tool_calls": None},
        ]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(
        agent.chat_stream([{"role": "user", "content": "按右侧三件套创建任务并派发"}], mode="fast")
    )

    finished = [e for e in events if e["event"] == "step_finished"]
    assert [e["data"]["status"] for e in finished] == ["success", "success"]

    # 任务真实落库且携带三件套
    from sqlalchemy import select

    from app.models.task import Task

    rows = (
        (await db_session.execute(select(Task).where(Task.title.contains("登录页面任务"))))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].specification is not None
    assert rows[0].status == "pending"

    # 创建轮的提示词中注入了生成后的三件套上下文
    create_round_messages = client.calls[2]["messages"]
    assert any("任务规范" in (m.get("content") or "") for m in create_round_messages)


async def test_build_messages_replays_tool_trajectory(db_session: AsyncSession) -> None:
    """UT-7（ADR-040）：历史中的工具轨迹（tool_calls + tool 消息）原样回流 LLM。"""
    agent = OrchestratorAgent(db_session)
    tool_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "find_idle_nodes", "arguments": "{}"},
    }
    messages = [
        {"role": "user", "content": "看下空闲节点"},
        {"role": "assistant", "content": "", "tool_calls": [tool_call], "steps": [{"x": 1}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "节点 code 空闲"},
        {"role": "assistant", "content": "当前空闲节点是 code", "thinking": "思考内容"},
    ]

    prompt = agent._build_messages(messages, None, [], [])

    # system 首条，其余按序透传
    assert prompt[0]["role"] == "system"
    assistant_with_tools = prompt[2]
    assert assistant_with_tools["role"] == "assistant"
    assert assistant_with_tools["tool_calls"] == [tool_call]
    # steps/thinking 为展示层数据，不进 LLM 请求
    assert "steps" not in assistant_with_tools
    assert prompt[3] == {"role": "tool", "tool_call_id": "call-1", "content": "节点 code 空闲"}
    assert prompt[4] == {"role": "assistant", "content": "当前空闲节点是 code"}
    assert "thinking" not in prompt[4]


async def test_build_messages_legacy_history_compatible(db_session: AsyncSession) -> None:
    """UT-8（ADR-040）：旧格式历史（无 tool_calls）回流行为与现状一致。"""
    agent = OrchestratorAgent(db_session)
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "你好", "thinking": "t", "steps": [{"s": 1}]},
        {"role": "user", "content": "继续"},
    ]

    prompt = agent._build_messages(messages, None, [], [])

    assert prompt[1:] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "你好"},
        {"role": "user", "content": "继续"},
    ]


async def test_build_messages_patches_missing_tool_response(db_session: AsyncSession) -> None:
    """UT-9（ADR-040）：assistant 带 tool_calls 但缺 tool 响应的脏数据自动补空。"""
    agent = OrchestratorAgent(db_session)
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-a", "function": {"name": "find_idle_nodes", "arguments": "{}"}},
                {"id": "call-b", "function": {"name": "get_task_status", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call-a", "content": "结果A"},
        # call-b 的 tool 响应缺失（脏数据）
        {"role": "user", "content": "继续"},
    ]

    prompt = agent._build_messages(messages, None, [], [])

    roles = [m["role"] for m in prompt[1:]]
    assert roles == ["user", "assistant", "tool", "tool", "user"]
    patched = prompt[3]
    assert patched == {"role": "tool", "tool_call_id": "call-b", "content": ""}


async def test_tool_round_emits_trajectory_event(db_session: AsyncSession) -> None:
    """UT-16（ADR-040）：工具轮产出 _history_trajectory 内部事件供端点落库。"""
    client = FakeDeepSeekClient(
        [
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-t",
                        "function": {"name": "find_idle_nodes", "arguments": "{}"},
                    }
                ],
            },
            {"content": "回答", "tool_calls": None},
        ]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    trajectory_events = [e for e in events if e["event"] == "_history_trajectory"]
    assert len(trajectory_events) == 1
    messages = trajectory_events[0]["data"]["messages"]
    assert messages[0]["role"] == "assistant"
    assert messages[0]["tool_calls"][0]["id"] == "call-t"
    # tool 消息 content 为真实技能执行结果摘要（find_idle_nodes 真实执行）
    assert messages[1]["role"] == "tool"
    assert messages[1]["tool_call_id"] == "call-t"
    assert messages[1]["content"]
    # 内部事件在最终回答轮不重复产出（工具轮之后无新一轮工具调用）
    assert events[-1]["event"] == "done"


async def test_context_usage_event_with_real_usage(db_session: AsyncSession) -> None:
    """UT-10（ADR-041）：流结束按真实 usage 推送 context_usage 事件。"""
    client = FakeDeepSeekClient(
        [{"content": "回答", "tool_calls": None}],
        usage={"prompt_tokens": 123, "completion_tokens": 10, "total_tokens": 133},
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    usage_events = [e for e in events if e["event"] == "context_usage"]
    assert len(usage_events) == 1
    data = usage_events[0]["data"]
    assert data["used_tokens"] == 123
    assert data["window_tokens"] == 65536
    assert data["percent"] == round(123 / 65536 * 100, 1)


async def test_context_usage_estimated_fallback(db_session: AsyncSession) -> None:
    """UT-11（ADR-041）：usage 缺失时按 chars/4 估算回落。"""
    client = FakeDeepSeekClient([{"content": "回答", "tool_calls": None}], usage=None)
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    usage_events = [e for e in events if e["event"] == "context_usage"]
    assert len(usage_events) == 1
    assert usage_events[0]["data"]["used_tokens"] > 0


async def test_overflow_triggers_compaction_and_retry(
    db_session: AsyncSession,
) -> None:
    """UT-12（ADR-041）：overflow 400 → 强制压缩 → 重跑本轮成功不中断。

    建真实会话行使 building_session 生效；fake 首轮流式调用抛 overflow，
    压缩（chat_completion_messages）后第二次流式调用成功。
    """
    from app.models.task_building_session import TaskBuildingSession

    session_row = TaskBuildingSession(
        title="overflow 测试会话",
        messages=[{"role": "user", "content": "hi"}],
        mode="fast",
    )
    db_session.add(session_row)
    await db_session.commit()

    client = FakeDeepSeekClient(
        [
            {"overflow": True},  # 首轮流式调用溢出
            {"content": "压缩后成功回答", "tool_calls": None},
        ]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    # 历史总 token 需超过 recent 保留量（8000）才能切分出待摘要的 head
    long_history = [
        {"role": "user", "content": f"历史消息{i}，" + "细节" * 6000} for i in range(12)
    ]
    events = await _collect(
        agent.chat_stream(
            long_history,
            mode="fast",
            session_id=str(session_row.id),
        )
    )

    names = [e["event"] for e in events]
    assert names[-1] == "done"
    assert not any(e["event"] == "error" for e in events)
    assert "压缩后成功回答" in "".join(e["data"]["delta"] for e in events if e["event"] == "chunk")
    # 三次 LLM 调用：流式(溢出) → 摘要(压缩) → 流式(重跑成功)
    assert len(client.calls) == 3
    # 重跑轮的 prompt 首条历史为压缩 checkpoint（投影生效）
    retried_history = client.calls[2]["messages"][1]
    assert "<conversation-checkpoint>" in retried_history["content"]
    # 压缩点已落库
    await db_session.refresh(session_row)
    assert session_row.context_compaction is not None
    assert session_row.context_compaction["summary_text"].startswith("## Objective")


# ---------------------------------------------------------------------------
# ADR-043 执行保护：技能超时 + Doom Loop 检测
# ---------------------------------------------------------------------------


def test_tool_calls_signature_normalizes_argument_order() -> None:
    """签名规范化：参数键序无关（UT-1）；非法 JSON 回退原样字符串（UT-2）。"""
    from app.agents.orchestrator import _tool_calls_signature

    a = [{"function": {"name": "s", "arguments": '{"a":1,"b":2}'}}]
    b = [{"function": {"name": "s", "arguments": '{"b":2,"a":1}'}}]
    assert _tool_calls_signature(a) == _tool_calls_signature(b)

    bad = [{"function": {"name": "s", "arguments": "not-json{"}}]
    assert "not-json{" in _tool_calls_signature(bad)


def _same_tool_call(call_id: str = "call-1") -> dict[str, Any]:
    return {
        "id": call_id,
        "function": {"name": "find_idle_nodes", "arguments": '{"agent_type":"code"}'},
    }


async def test_skill_execution_timeout_returns_failure_and_continues(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """挂起技能超过预算被中止：step_finished(failed) + 失败 tool 消息回传，
    对话循环继续（UT-3）。"""
    import asyncio

    from app.skills.base import SkillResult

    class _HangingSkill:
        name = "hanging_skill"
        display_name = "挂起技能"

        async def execute(self, session: AsyncSession, context: Any):
            yield {"event": "step_progress", "data": {"text": "开始挂起"}}
            await asyncio.sleep(10)
            yield {  # pragma: no cover - 超时后不可达
                "event": "_result",
                "data": SkillResult(success=True, summary="不应到达"),
            }

    monkeypatch.setattr(
        "app.agents.orchestrator.find_skill",
        lambda key: _HangingSkill() if key == "hanging_skill" else None,
    )
    monkeypatch.setattr(
        "app.agents.orchestrator.get_settings",
        _fake_settings(skill_execution_timeout_seconds=0.2),
    )

    client = FakeDeepSeekClient(
        [
            {
                "content": None,
                "tool_calls": [
                    {"id": "call-t", "function": {"name": "hanging_skill", "arguments": "{}"}}
                ],
            },
            {"content": "技能超时后仍能回答", "tool_calls": None},
        ]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    finished = next(e for e in events if e["event"] == "step_finished")["data"]
    assert finished["status"] == "failed"
    assert "超时" in finished["result_summary"]
    # 失败结果以 tool 消息回传（协议防 400），对话继续到最终回答
    tool_msg = client.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool"
    assert "超时" in tool_msg["content"]
    assert events[-1]["event"] == "done"


async def test_doom_loop_three_identical_rounds_force_finish(
    db_session: AsyncSession,
) -> None:
    """连续 3 轮完全相同调用：第 3 轮不执行、warning 下发、强制收尾（UT-5）。"""
    same_call = _same_tool_call()
    client = FakeDeepSeekClient(
        [
            {"content": None, "tool_calls": [dict(same_call)]},
            {"content": None, "tool_calls": [dict(same_call)]},
            {"content": None, "tool_calls": [dict(same_call)]},
            {"content": "基于已有结果直接回答", "tool_calls": None},
        ]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    # 前 2 轮执行（每轮一次 LLM 调用），第 3 轮 doom 命中后收尾（第 4 次调用）
    assert len(client.calls) == 4
    assert client.calls[-1]["tools"] is None
    assert any(
        "完全相同的工具调用" in str(m.get("content") or "") for m in client.calls[-1]["messages"]
    )

    warnings = [e for e in events if e["event"] == "warning"]
    assert warnings and "重复" in warnings[0]["data"]["message"]

    finished_events = [e["data"] for e in events if e["event"] == "step_finished"]
    # 第 3 轮生成跳过卡片（failed + 未执行说明），真实执行的只有 2 次
    assert any("未执行" in f["result_summary"] for f in finished_events)
    executed = [f for f in finished_events if "未执行" not in f["result_summary"]]
    assert len(executed) == 2

    assert events[-1]["event"] == "done"
    assert "基于已有结果直接回答" in "".join(
        e["data"]["delta"] for e in events if e["event"] == "chunk"
    )


async def test_doom_loop_below_threshold_not_triggered(db_session: AsyncSession) -> None:
    """连续 2 轮相同后第 3 轮换技能：不触发收尾，全部执行（UT-6）。"""
    client = FakeDeepSeekClient(
        [
            {"content": None, "tool_calls": [_same_tool_call()]},
            {"content": None, "tool_calls": [_same_tool_call("call-2")]},
            {
                "content": None,
                "tool_calls": [
                    {"id": "call-3", "function": {"name": "get_task_status", "arguments": "{}"}}
                ],
            },
            {"content": "正常回答", "tool_calls": None},
        ]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    assert not any(e["event"] == "warning" for e in events)
    assert events[-1]["event"] == "done"
    # 4 次 LLM 调用 = 3 个工具轮 + 1 个自然收尾轮
    assert len(client.calls) == 4
    assert client.calls[2]["tools"] is not None


async def test_doom_loop_alternating_calls_not_triggered(db_session: AsyncSession) -> None:
    """交替重复 A,B,A,B,A：签名窗口不连续相同，不误伤（UT-7）。"""
    call_a = _same_tool_call()
    call_b = {"id": "call-b", "function": {"name": "get_task_status", "arguments": "{}"}}
    client = FakeDeepSeekClient(
        [
            {"content": None, "tool_calls": [dict(call_a)]},
            {"content": None, "tool_calls": [dict(call_b)]},
            {"content": None, "tool_calls": [dict(call_a)]},
            {"content": None, "tool_calls": [dict(call_b)]},
            {"content": None, "tool_calls": [dict(call_a)]},
            {"content": "交替不误伤回答", "tool_calls": None},
        ]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    assert not any(e["event"] == "warning" for e in events)
    assert events[-1]["event"] == "done"


async def test_doom_loop_different_args_not_triggered(db_session: AsyncSession) -> None:
    """同名技能但参数不同：签名不同，不触发（UT-8）。"""
    client = FakeDeepSeekClient(
        [
            {"content": None, "tool_calls": [_same_tool_call()]},
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-2",
                        "function": {
                            "name": "find_idle_nodes",
                            "arguments": '{"agent_type":"video"}',
                        },
                    }
                ],
            },
            {"content": None, "tool_calls": [_same_tool_call("call-3")]},
            {"content": "参数变化不误伤回答", "tool_calls": None},
        ]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    assert not any(e["event"] == "warning" for e in events)
    assert events[-1]["event"] == "done"


# ---------------------------------------------------------------------------
# ADR-044 LLM 客户端韧性：对话降级链
# ---------------------------------------------------------------------------


async def _seed_llm_providers(db_session: AsyncSession, models: list[str]) -> None:
    """清空并按序插入 llm_providers 测试行（首行即使用中）。"""
    from sqlalchemy import delete

    from app.models.llm_provider import LLMProvider

    await db_session.execute(delete(LLMProvider))
    for index, model_name in enumerate(models):
        db_session.add(
            LLMProvider(
                provider=f"p{index}",
                model=model_name,
                api_key=f"sk-{index}",
                base_url=f"https://api-{index}.test",
                is_active=index == 0,
            )
        )
    await db_session.commit()


async def test_first_call_failure_falls_back_to_other_model(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首轮 LLM 调用重试耗尽：降级到其他模型继续对话（UT-8）。"""
    await _seed_llm_providers(db_session, ["test-model", "fallback-model"])

    primary = FakeDeepSeekClient([{"api_error": (503, "service unavailable")}])
    fallback = FakeDeepSeekClient([{"content": "降级后回答", "tool_calls": None}])
    fallback._config.model = "fallback-model"
    monkeypatch.setattr(
        "app.agents.orchestrator.DeepSeekClient",
        lambda config, scene=None: fallback,
    )
    monkeypatch.setattr(
        "app.agents.orchestrator.get_settings",
        _fake_settings(llm_retry_max_attempts=1),
    )

    agent = OrchestratorAgent(db_session, deepseek_client=primary)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    warnings = [e for e in events if e["event"] == "warning"]
    assert warnings and "降级" in warnings[0]["data"]["message"]
    # 降级后 meta 更新模型显示（第二 个 meta，model=fallback-model）
    metas = [e for e in events if e["event"] == "meta"]
    assert metas[-1]["data"]["model"] == "fallback-model"
    assert "降级后回答" in "".join(e["data"]["delta"] for e in events if e["event"] == "chunk")
    assert events[-1]["event"] == "done"


async def test_no_other_model_no_fallback(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """仅配置一个模型时：失败直接报错，无降级提示（UT-9）。"""
    await _seed_llm_providers(db_session, ["test-model"])
    monkeypatch.setattr(
        "app.agents.orchestrator.get_settings",
        _fake_settings(llm_retry_max_attempts=1),
    )

    client = FakeDeepSeekClient([{"api_error": (503, "service unavailable")}])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    assert not any(e["event"] == "warning" for e in events)
    assert any(e["event"] == "error" for e in events)
    assert events[-1]["event"] == "done"


async def test_fallback_failure_reports_error(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """降级模型也失败：报错结束，不无限降级（UT-10）。"""
    await _seed_llm_providers(db_session, ["test-model", "fallback-model"])

    primary = FakeDeepSeekClient([{"api_error": (503, "primary down")}])
    fallback = FakeDeepSeekClient([{"api_error": (503, "fallback down")}])
    fallback._config.model = "fallback-model"
    monkeypatch.setattr(
        "app.agents.orchestrator.DeepSeekClient",
        lambda config, scene=None: fallback,
    )
    monkeypatch.setattr(
        "app.agents.orchestrator.get_settings",
        _fake_settings(llm_retry_max_attempts=1),
    )

    agent = OrchestratorAgent(db_session, deepseek_client=primary)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    # 仅一次降级提示，最终 error 结束
    warnings = [e for e in events if e["event"] == "warning"]
    assert len(warnings) == 1
    assert any(e["event"] == "error" for e in events)
    assert events[-1]["event"] == "done"


# ---------------------------------------------------------------------------
# ADR-045 Steering：执行中插话边界提升 + 配额重置
# ---------------------------------------------------------------------------


async def test_tool_round_boundary_steering_applied(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工具轮边界提升插话：下一轮 prompt 含插话 user 消息（UT-5）。"""
    from app.services import steering_service

    session_id = "11111111-1111-1111-1111-111111111111"
    client = FakeDeepSeekClient(
        [
            {"content": None, "tool_calls": [_same_tool_call()]},
            # 轮 2：回答中应参考插话（prompt 断言为主，正文随意）
            {"content": "收到插话，已调整", "tool_calls": None},
        ]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]

    # 流开始后（open 在 meta 前）投递插话：后台异步触发一次
    async def _steer_later() -> None:
        await asyncio.sleep(0.05)
        assert steering_service.enqueue(session_id, "请改用方案B") is True

    steer_task = asyncio.create_task(_steer_later())
    events = await _collect(
        agent.chat_stream(
            [{"role": "user", "content": "开始"}],
            mode="fast",
            session_id=session_id,
        )
    )
    await steer_task

    # steering_applied 事件下发
    applied = [e for e in events if e["event"] == "steering_applied"]
    assert applied and applied[0]["data"]["content"] == "请改用方案B"
    # 第二轮流式调用的 prompt 含插话 user 消息（位于 assistant(tool_calls)/tool 之后）
    second_call_messages = client.calls[1]["messages"]
    roles = [m["role"] for m in second_call_messages]
    assert roles[-2:] == ["tool", "user"]
    assert second_call_messages[-1]["content"] == "请改用方案B"
    # 内部落库事件存在且不透传给前端事件流以外
    assert any(e["event"] == "_steering_messages" for e in events)
    # 流结束队列已注销
    assert steering_service.enqueue(session_id, "迟到的") is False


async def test_steering_before_final_answer_extends_conversation(
    db_session: AsyncSession,
) -> None:
    """收尾前提升：无工具调用直接回答时插话延续对话而非 done（UT-6）。"""
    from app.services import steering_service

    session_id = "22222222-2222-2222-2222-222222222222"
    client = FakeDeepSeekClient(
        [
            {"content": "第一段回答", "tool_calls": None},
            {"content": "基于插话的补充回答", "tool_calls": None},
        ]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]

    async def _steer_later() -> None:
        await asyncio.sleep(0.05)
        steering_service.enqueue(session_id, "再补充一点")

    steer_task = asyncio.create_task(_steer_later())
    events = await _collect(
        agent.chat_stream(
            [{"role": "user", "content": "开始"}],
            mode="fast",
            session_id=session_id,
        )
    )
    await steer_task

    # 两次 LLM 调用：首次回答 → 插话提升 → 续答
    assert len(client.calls) == 2
    assert client.calls[1]["messages"][-1] == {"role": "user", "content": "再补充一点"}
    assert client.calls[1]["messages"][-2] == {"role": "assistant", "content": "第一段回答"}
    reply = "".join(e["data"]["delta"] for e in events if e["event"] == "chunk")
    assert "第一段回答" in reply and "补充回答" in reply
    assert events[-1]["event"] == "done"


async def test_steering_resets_round_quota(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配额重置：max_rounds=2 且每轮边界投递插话 → 持续对话不止 2 轮（UT-7）。"""
    from app.services import steering_service

    session_id = "33333333-3333-3333-3333-333333333333"
    monkeypatch.setattr(
        "app.agents.orchestrator.get_settings",
        _fake_settings(orchestrator_max_tool_rounds=2),
    )

    class _ToolThenAnswerClient(FakeDeepSeekClient):
        """轮 1/2 调工具，轮 3+ 纯文本；投递计数达 3 次后停止投递。"""

        def __init__(self) -> None:
            super().__init__([])
            self.steer_count = 0

        async def chat_completion_stream_raw(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            tool_choice: str | None = None,
            reasoning: bool = False,
            thinking: bool | None = None,
            max_tokens: int | None = None,
        ) -> AsyncGenerator[dict[str, Any], None]:
            self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
            self._cursor += 1
            if self.steer_count < 3 and steering_service.enqueue(
                session_id, f"插话{self.steer_count}"
            ):
                self.steer_count += 1
            if len(self.calls) <= 2 or self.steer_count > 3:
                yield {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [_same_tool_call()],
                    },
                }
                return
            yield {"type": "content_delta", "delta": "最终回答"}
            yield {"type": "message", "message": {"role": "assistant", "content": "最终回答"}}

    client = _ToolThenAnswerClient()
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(
        agent.chat_stream(
            [{"role": "user", "content": "开始"}],
            mode="fast",
            session_id=session_id,
        )
    )

    # 配额被反复重置：调用次数超过 max_rounds(2)
    assert len(client.calls) > 2
    assert events[-1]["event"] == "done"


async def test_non_overflow_400_does_not_compact(db_session: AsyncSession) -> None:
    """UT-13（ADR-041）：普通 400 不触发压缩，直接 error 收尾。"""

    class _BadKeyClient(FakeDeepSeekClient):
        async def chat_completion_stream_raw(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            tool_choice: str | None = None,
            reasoning: bool = False,
            thinking: bool | None = None,
            max_tokens: int | None = None,
        ) -> AsyncGenerator[dict[str, Any], None]:
            self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
            raise DeepSeekAPIError(status_code=400, message="Authentication Fails")
            yield  # pragma: no cover

    client = _BadKeyClient([])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    assert any(e["event"] == "error" for e in events)
    assert events[-1]["event"] == "done"


# ---------------------------------------------------------------------------
# ADR-042：环境上下文注入
# ---------------------------------------------------------------------------


async def _make_env_session(db_session: AsyncSession) -> Any:
    from app.models.task_building_session import TaskBuildingSession

    row = TaskBuildingSession(title="环境测试会话", messages=[], mode="fast")
    db_session.add(row)
    await db_session.commit()
    return row


async def test_environment_baseline_injected_first_round(db_session: AsyncSession) -> None:
    """IT-1（ADR-042）：首轮 system 含 <environment> 块且快照落库。"""
    session_row = await _make_env_session(db_session)
    client = FakeDeepSeekClient([{"content": "回答", "tool_calls": None}])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    await _collect(
        agent.chat_stream(
            [{"role": "user", "content": "hi"}], mode="fast", session_id=str(session_row.id)
        )
    )

    system_content = client.calls[0]["messages"][0]["content"]
    assert "<environment>" in system_content
    assert "当前时间：" in system_content
    await db_session.refresh(session_row)
    assert session_row.context_environment is not None
    assert session_row.context_environment["block"]


async def test_environment_unchanged_no_update_injection(db_session: AsyncSession) -> None:
    """IT-2（ADR-042）：环境未变时第二轮 system 与首轮一致、无增量消息。"""
    session_row = await _make_env_session(db_session)
    client = FakeDeepSeekClient(
        [
            {"content": "第一轮", "tool_calls": None},
            {"content": "第二轮", "tool_calls": None},
        ]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    await _collect(
        agent.chat_stream(
            [{"role": "user", "content": "一"}], mode="fast", session_id=str(session_row.id)
        )
    )
    await _collect(
        agent.chat_stream(
            [
                {"role": "user", "content": "一"},
                {"role": "assistant", "content": "第一轮"},
                {"role": "user", "content": "二"},
            ],
            mode="fast",
            session_id=str(session_row.id),
        )
    )

    # 两轮 system 均含环境块（快照版本）；第二轮无增量标签
    first_system = client.calls[0]["messages"][0]["content"]
    second_system = client.calls[1]["messages"][0]["content"]
    assert "<environment>" in first_system
    assert "<environment-update>" not in second_system
    assert "当前时间：" in second_system


async def test_environment_change_injects_update(db_session: AsyncSession) -> None:
    """IT-3（ADR-042）：环境变化时 prompt 尾部出现增量消息且快照更新。"""
    from datetime import UTC, datetime

    session_row = await _make_env_session(db_session)
    # 预置一份"过时"快照，模拟环境已变化
    session_row.context_environment = {
        "block": "<environment>\n当前时间：2020-01-01（星期三）00时\n</environment>",
        "updated_at": datetime(2020, 1, 1, tzinfo=UTC).isoformat(),
    }
    await db_session.commit()

    client = FakeDeepSeekClient([{"content": "回答", "tool_calls": None}])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    await _collect(
        agent.chat_stream(
            [{"role": "user", "content": "hi"}], mode="fast", session_id=str(session_row.id)
        )
    )

    # system 使用快照版本（Baseline 不可变）
    system_content = client.calls[0]["messages"][0]["content"]
    assert "2020-01-01" in system_content
    # 尾部追加增量消息（携带新环境）
    last_message = client.calls[0]["messages"][-1]
    assert last_message["role"] == "system"
    assert "<environment-update>" in last_message["content"]
    await db_session.refresh(session_row)
    assert "2020-01-01" not in session_row.context_environment["block"]


async def test_environment_no_session_skips_snapshot(db_session: AsyncSession) -> None:
    """IT-4（ADR-042）：无会话对话注入实时环境块但不落快照。"""
    client = FakeDeepSeekClient([{"content": "回答", "tool_calls": None}])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    assert events[-1]["event"] == "done"
    system_content = client.calls[0]["messages"][0]["content"]
    assert "<environment>" in system_content


# ---------------------------------------------------------------------------
# ADR-041 修订 2026-09-27（P0）：计量校准 + 触发度量对象 + 事件条件化
# ---------------------------------------------------------------------------


async def test_no_compacting_event_when_cutoff_closed(
    db_session: AsyncSession,
) -> None:
    """UT-25 / IT-6（P0）：压缩点已覆盖全部历史时不再提示「正在压缩」。

    回归对象：修复前判定读会话原始 messages，压缩后估算不回落，越线后每轮都
    下发 context_compacting（用户可见的「每次都在压缩」）。此处预置压缩点
    message_count 已与历史长度相等 —— 修复后 should_compact_now 为假。
    """
    from app.models.task_building_session import TaskBuildingSession

    history = [{"role": "user", "content": f"历史消息{i}，" + "细节" * 6000} for i in range(12)]
    session_row = TaskBuildingSession(
        title="压缩点已覆盖",
        messages=history,
        mode="fast",
        context_compaction={
            "summary_text": "## Objective\n既有摘要",
            "message_count": len(history),
            "created_at": "2026-09-27T00:00:00+00:00",
        },
    )
    db_session.add(session_row)
    await db_session.commit()

    client = FakeDeepSeekClient([{"content": "回答", "tool_calls": None}])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream(history, mode="fast", session_id=str(session_row.id)))

    names = [event["event"] for event in events]
    assert "context_compacting" not in names
    # compacted 事件仍成对语义完整（compacted=false）
    compacted = [e for e in events if e["event"] == "context_compacted"]
    assert len(compacted) == 1
    assert compacted[0]["data"]["compacted"] is False
    # 未触发摘要调用：本轮只有 1 次 LLM 流式调用
    assert len(client.calls) == 1
    assert events[-1]["event"] == "done"


async def test_compacting_event_when_head_grew(
    db_session: AsyncSession,
) -> None:
    """UT-26（P0）：压力越线 ∧ head 确有新增 → 事件成对下发并真的压缩。

    前提构造（严谨化）：压缩点存在时请求体是「checkpoint + 压缩点之后的消息」，
    压力口径必须自身越线才会进入推进判定（修复后判定不再读原始全量历史）。
    故历史取足够重（20×6000 字 ≈ 6 万估算 token），并令压缩点落后 1 条 head。
    """
    from app.models.task_building_session import TaskBuildingSession
    from app.services.context_compaction_service import (
        compaction_threshold,
        estimate_messages_tokens,
        recent_keep_tokens_for,
    )

    # 每条约 6000 字符 ≈ 3000 估算 token；recent 预算 8000 token
    history = [{"role": "user", "content": f"历史消息{i}，" + "细节" * 3000} for i in range(20)]
    # 压缩点落在很靠前的位置：其后的新内容（17 条 ≈ 5.1 万估算 token）
    # 重新把请求推过触发线，且原始历史的 head 确有新增 —— 这正是
    # 「多轮对话再次撑大上下文」的真实形态
    watermark = 3
    assert estimate_messages_tokens(history[watermark:]) > compaction_threshold(65536), (
        "用例前提不成立：压缩点之后的新内容不足以让请求越线"
    )
    # recent 预算 8000 token / 每条 ≈3000 token → 原始 head 必远超 watermark
    assert watermark > recent_keep_tokens_for(65536) // 3000, "用例前提：watermark 需小于 head 长度"
    session_row = TaskBuildingSession(
        title="压缩点落后",
        messages=history,
        mode="fast",
        context_compaction={
            "summary_text": "## Objective\n旧摘要",
            "message_count": watermark,
            "created_at": "2026-09-27T00:00:00+00:00",
        },
    )
    db_session.add(session_row)
    await db_session.commit()

    client = FakeDeepSeekClient([{"content": "回答", "tool_calls": None}])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream(history, mode="fast", session_id=str(session_row.id)))

    names = [event["event"] for event in events]
    assert "context_compacting" in names, (
        f"用例前提不成立：未见推进判定（watermark={watermark}）；事件序列 {names}"
    )
    compacted = [e for e in events if e["event"] == "context_compacted"]
    assert compacted and compacted[-1]["data"]["compacted"] is True
    # 发生了摘要调用（chat_completion_messages）+ 流式调用
    assert len(client.calls) == 2
    await db_session.refresh(session_row)
    assert session_row.context_compaction["message_count"] > watermark


async def test_calibration_persists_est_chars_per_token(
    db_session: AsyncSession,
) -> None:
    """UT-27 / IT-5（P0）：真实 usage 回写校准密度，供下一轮判定使用。

    观测密度 = measured_chars / prompt_tokens；首次以种子常数 2 起算并做
    EMA（α=0.3）。此处 prompt_tokens 取小于门槛（2000）之外的正常值，
    并断言写回值落在夹取区间内且与公式一致。
    """
    from app.models.task_building_session import TaskBuildingSession
    from app.services.context_compaction_service import (
        CALIBRATION_ALPHA,
        CALIBRATION_MIN_CHARS_PER_TOKEN,
        CHARS_PER_TOKEN,
        estimate_chars_per_token,
        measure_payload_chars,
    )

    session_row = TaskBuildingSession(
        title="校准回写",
        messages=[{"role": "user", "content": "hi"}],
        mode="fast",
        context_compaction={
            "summary_text": "## Objective\n既有摘要",
            "message_count": 0,
            "created_at": "2026-09-27T00:00:00+00:00",
        },
    )
    db_session.add(session_row)
    await db_session.commit()

    usage = {"prompt_tokens": 5000, "completion_tokens": 10, "total_tokens": 5010}
    client = FakeDeepSeekClient([{"content": "回答", "tool_calls": None}], usage=usage)
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    await _collect(
        agent.chat_stream(
            [{"role": "user", "content": "hi"}], mode="fast", session_id=str(session_row.id)
        )
    )

    await db_session.refresh(session_row)
    stored = estimate_chars_per_token(session_row.context_compaction)
    measured_chars = measure_payload_chars(
        client.calls[0]["messages"],
        client.calls[0]["tools"],
        client.calls[0]["messages"][0]["content"],
    )
    observed = measured_chars / usage["prompt_tokens"]
    expected = float(CHARS_PER_TOKEN) + CALIBRATION_ALPHA * (
        max(CALIBRATION_MIN_CHARS_PER_TOKEN, min(6.0, observed)) - float(CHARS_PER_TOKEN)
    )
    assert stored == pytest.approx(expected, rel=1e-6)
    # 原有压缩点字段不被覆盖（校准写回是合并而非替换）
    assert session_row.context_compaction["summary_text"] == "## Objective\n既有摘要"
    assert "est_sample_at" in session_row.context_compaction


async def test_calibration_skipped_without_usage_or_compaction(
    db_session: AsyncSession,
) -> None:
    """UT-27 边界（P0）：usage 缺失时保留既有校准；无压缩点时不写入校准键。"""
    from app.models.task_building_session import TaskBuildingSession

    # 场景 A：有压缩点 + usage 缺失 → 保留既有校准值 2.4
    row_with_compaction = TaskBuildingSession(
        title="无 usage",
        messages=[{"role": "user", "content": "hi"}],
        mode="fast",
        context_compaction={"summary_text": "旧", "message_count": 0, "est_chars_per_token": 2.4},
    )
    # 场景 B：无压缩点 + 有 usage → 不创建只有校准键的压缩点对象
    row_without_compaction = TaskBuildingSession(
        title="无压缩点",
        messages=[{"role": "user", "content": "hi"}],
        mode="fast",
    )
    db_session.add_all([row_with_compaction, row_without_compaction])
    await db_session.commit()

    client = FakeDeepSeekClient([{"content": "回答", "tool_calls": None}], usage=None)
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    await _collect(
        agent.chat_stream(
            [{"role": "user", "content": "hi"}],
            mode="fast",
            session_id=str(row_with_compaction.id),
        )
    )
    await db_session.refresh(row_with_compaction)
    assert row_with_compaction.context_compaction["est_chars_per_token"] == 2.4

    client2 = FakeDeepSeekClient(
        [{"content": "回答", "tool_calls": None}],
        usage={"prompt_tokens": 5000, "completion_tokens": 5, "total_tokens": 5005},
    )
    agent2 = OrchestratorAgent(db_session, deepseek_client=client2)  # type: ignore[arg-type]
    await _collect(
        agent2.chat_stream(
            [{"role": "user", "content": "hi"}],
            mode="fast",
            session_id=str(row_without_compaction.id),
        )
    )
    await db_session.refresh(row_without_compaction)
    assert row_without_compaction.context_compaction is None


# ---------------------------------------------------------------------------
# ADR-054（P1）：压缩切点落在工具轮时对话不中断
# ---------------------------------------------------------------------------


async def test_compaction_cutoff_inside_tool_turn_does_not_break_chat(
    db_session: AsyncSession,
) -> None:
    """IT-7（ADR-054）：压缩切点必然落在 tool 上时，对话正常收尾（不再 400 中断）。

    回归对象：修复前投影序列以孤儿 `tool` 消息开头，provider 返回
    「Messages with role 'tool' must be a response to a preceding message with
    'tool_calls'」，对话直接 error 收尾。修复后切点前移到工具轮之前。
    """
    from app.models.task_building_session import TaskBuildingSession
    from app.services.context_compaction_service import (
        compaction_threshold,
        estimate_messages_tokens,
        recent_keep_tokens_for,
        safe_cutoff,
        split_for_compaction,
    )

    def _build(tool_turn_at: int | None) -> list[dict[str, Any]]:
        """构造 20 条等重历史；``tool_turn_at`` 处替换为一轮工具调用。"""
        built: list[dict[str, Any]] = []
        for index in range(20):
            if index == tool_turn_at:
                built.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": f"c{index}", "function": {"name": "t"}}],
                    }
                )
                built.append(
                    {"role": "tool", "tool_call_id": f"c{index}", "content": "结果A" + "a" * 6000}
                )
                built.append(
                    {"role": "tool", "tool_call_id": f"c{index}", "content": "结果B" + "b" * 6000}
                )
                continue
            built.append({"role": "user", "content": f"m{index}，" + "细节" * 3000})
        return built

    keep = recent_keep_tokens_for(65536)
    # 运行时实测：先构造只含 user 的基线，拿到切点下标
    baseline = _build(None)
    baseline_head, _ = split_for_compaction(baseline, recent_keep_tokens=keep)
    split_index = len(baseline_head)
    assert 0 < split_index < len(baseline), "用例前提：需能切出 head 与 recent"

    # 把工具轮放在切点处（替换 1 条 user 为 3 条工具轮消息，前移后续下标）
    history = _build(split_index)

    # 用例前提：原始切分点确实落在 tool 消息上（否则本用例无意义）
    head, _ = split_for_compaction(history, recent_keep_tokens=keep)
    assert 0 < len(head) < len(history)
    assert history[len(head)]["role"] == "tool", (
        f"用例前提不成立：切点角色为 {history[len(head)]['role']}，需落在 tool 上"
    )
    assert safe_cutoff(history, len(head)) < len(head), "用例前提：安全切点应前移"
    assert estimate_messages_tokens(history) > compaction_threshold(65536)

    session_row = TaskBuildingSession(title="切点落工具轮", messages=history, mode="fast")
    db_session.add(session_row)
    await db_session.commit()

    client = FakeDeepSeekClient([{"content": "压缩后继续回答", "tool_calls": None}])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream(history, mode="fast", session_id=str(session_row.id)))

    # 无 error，正常收尾
    names = [event["event"] for event in events]
    assert "error" not in names, f"对话不应中断，事件序列 {names}"
    assert names[-1] == "done"
    assert "context_compacting" in names
    # 压缩确实发生，且落库切点已校正（角色不为 tool）
    await db_session.refresh(session_row)
    compaction = session_row.context_compaction
    assert compaction is not None
    assert compaction["cutoff_role"] != "tool"
    # 第二次 LLM 调用的请求体里，checkpoint 后首条历史消息不为 tool
    retried = client.calls[-1]["messages"]
    checkpoint_index = next(
        i for i, m in enumerate(retried) if "<conversation-checkpoint>" in str(m.get("content"))
    )
    assert retried[checkpoint_index + 1]["role"] != "tool"


async def test_legacy_dirty_cutoff_session_still_chats(
    db_session: AsyncSession,
) -> None:
    """IT-9（ADR-054）：存量脏压缩点（cutoff 落 tool）在新代码下也能正常对话。

    用户决策：不做存量数据修复。但投影侧动态 `safe_cutoff` 保证脏值不再产生
    协议非法请求，因此旧会话至少不会被 400 卡死。
    """
    from app.models.task_building_session import TaskBuildingSession
    from app.services.context_compaction_service import project_messages

    history: list[dict[str, Any]] = [
        {"role": "user", "content": "开头，" + "细节" * 2000},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "t"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "结果" + "a" * 4000},
        {"role": "tool", "tool_call_id": "c1", "content": "结果2" + "b" * 4000},
        {"role": "user", "content": "继续"},
    ]
    dirty = {
        "summary_text": "## Objective\n旧摘要",
        "message_count": 2,  # 落在 tool 上（P1 之前写入的脏值）
        "created_at": "2026-09-27T00:00:00+00:00",
    }
    session_row = TaskBuildingSession(
        title="存量脏点", messages=history, mode="fast", context_compaction=dirty
    )
    db_session.add(session_row)
    await db_session.commit()

    # 投影侧动态校正：首条历史不为 tool
    projected = project_messages(history, dirty)
    assert projected[1]["role"] != "tool"

    client = FakeDeepSeekClient([{"content": "回答", "tool_calls": None}])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream(history, mode="fast", session_id=str(session_row.id)))

    names = [event["event"] for event in events]
    assert "error" not in names, f"脏压缩点不应导致对话中断，事件序列 {names}"
    assert names[-1] == "done"


# ---------------------------------------------------------------------------
# ADR-056（P2）：reasoning_content 回流与窗口口径
# ---------------------------------------------------------------------------


class _ToolThenReasoningClient(FakeDeepSeekClient):
    """首轮返回 reasoning 增量 + 工具调用，次轮返回最终回答。

    记录每次调用的 ``tools``，供断言「带 tools 的请求才回传 reasoning_content」。
    """

    def __init__(self) -> None:
        super().__init__([])
        self.tool_args: list[Any] = []

    async def chat_completion_stream_raw(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        reasoning: bool = False,
        thinking: bool | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        self.tool_args.append(tools)
        self._cursor += 1
        if self._cursor == 1:
            yield {"type": "reasoning_delta", "delta": "我需要先看空闲节点。"}
            yield {"type": "reasoning_delta", "delta": "调用 find_idle_nodes。"}
            yield {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-r1",
                            "type": "function",
                            "function": {
                                "name": "find_idle_nodes",
                                "arguments": json.dumps({"agent_type": "code"}),
                            },
                        }
                    ],
                },
            }
            return
        yield {"type": "content_delta", "delta": "已完成"}
        yield {"type": "message", "message": {"role": "assistant", "content": "已完成"}}


async def test_reasoning_content_replayed_with_tools(
    db_session: AsyncSession,
) -> None:
    """UT-58/UT-61（ADR-056 D4）：带 tools 的请求回传 reasoning_content，且为拼接全文。"""
    client = _ToolThenReasoningClient()
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(
        agent.chat_stream([{"role": "user", "content": "看下空闲节点"}], mode="fast")
    )

    # 第二次调用仍带 tools：历史 assistant(tool_calls) 应带 reasoning_content
    second_messages = client.calls[1]["messages"]
    with_tools_entry = next(
        m for m in second_messages if m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert with_tools_entry["reasoning_content"] == "我需要先看空闲节点。调用 find_idle_nodes。"
    # thinking 事件已实时下发前端
    thinking = "".join(e["data"]["delta"] for e in events if e["event"] == "thinking")
    assert "find_idle_nodes" in thinking


async def test_reasoning_content_not_replayed_without_tools(
    db_session: AsyncSession,
) -> None:
    """UT-59（ADR-056 D4）：不带 tools 的请求不回传 reasoning_content。"""
    client = _ToolThenReasoningClient()
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    # 构造一轮「带 thinking 的 assistant + 无 tools 请求」直接验证 _build_messages
    history = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t"}}],
            "thinking": "推理内容",
        }
    ]
    with_tools = agent._build_messages(history, None, [], [], None, with_tools=True)
    without_tools = agent._build_messages(history, None, [], [], None, with_tools=False)
    assert with_tools[1]["reasoning_content"] == "推理内容"
    assert "reasoning_content" not in without_tools[1]


async def test_reasoning_content_absent_for_legacy_history(
    db_session: AsyncSession,
) -> None:
    """UT-60（ADR-056 守卫）：旧会话（assistant 无 thinking）回流行为不变。"""
    client = FakeDeepSeekClient([{"content": "回答", "tool_calls": None}])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    history = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "结果"},
    ]
    built = agent._build_messages(history, None, [], [], None, with_tools=True)
    assert all("reasoning_content" not in m for m in built)


async def test_window_1m_does_not_compact_at_low_usage(
    db_session: AsyncSession,
) -> None:
    """IT-10（ADR-056）：1M 窗口下低占用历史不触发压缩（窗口口径端到端）。"""
    from app.models.task_building_session import TaskBuildingSession

    history = [{"role": "user", "content": f"m{i}，" + "细节" * 3000} for i in range(6)]
    session_row = TaskBuildingSession(title="1M 窗口", messages=history, mode="fast")
    db_session.add(session_row)
    await db_session.commit()

    client = FakeDeepSeekClient([{"content": "回答", "tool_calls": None}], context_window=1000000)
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream(history, mode="fast", session_id=str(session_row.id)))

    names = [event["event"] for event in events]
    assert "context_compacting" not in names
    assert "error" not in names
    usage = next(e for e in events if e["event"] == "context_usage")["data"]
    assert usage["window_tokens"] == 1000000
    assert usage["percent"] < 10


# ---------------------------------------------------------------------------
# ADR-058：工具轮预算改为进度驱动（轮数上限降级为安全网 + 无新信息进度检测）
# ---------------------------------------------------------------------------


class _ScriptedSkillsClient(FakeDeepSeekClient):
    """按脚本每轮调用一个技能，用尽脚本后给出纯文本回答。

    ``tool_names`` 为逐轮技能名；每轮只发一个 tool_call。
    ``repeat`` 为 True 时用尽后从头循环（模拟「反复查同一批工具」）。

    注意：参数带**递增序号**（``{"n": <轮次>}``），使每轮签名都不同 ——
    否则连续 3 轮会被 Doom Loop（ADR-043）先命中，无进展检测（ADR-058）
    永远执行不到。「换了参数但无新信息」正是 ADR-058 要覆盖的形态。
    """

    def __init__(self, tool_names: list[str], repeat: bool = False) -> None:
        super().__init__([])
        self._tool_names = tool_names
        self._repeat = repeat
        self.tool_args: list[Any] = []

    async def chat_completion_stream_raw(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        reasoning: bool = False,
        thinking: bool | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        self.tool_args.append(tools)
        index = self._cursor
        self._cursor += 1
        if tools and (index < len(self._tool_names) or self._repeat):
            name = self._tool_names[index % len(self._tool_names)]
            yield {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call-{index}",
                            "type": "function",
                            "function": {"name": name, "arguments": f'{{"n": {index}}}'},
                        }
                    ],
                },
            }
            return
        text = "已基于现有信息给出最终回答"
        yield {"type": "content_delta", "delta": text}
        yield {"type": "message", "message": {"role": "assistant", "content": text}}


def test_round_budget_defaults() -> None:
    """UT-75（ADR-058）：默认上限 40、无进展阈值 4、警示比例 0.75。"""
    from app.core.config import get_settings

    settings = get_settings()
    assert settings.orchestrator_max_tool_rounds == 40
    assert settings.orchestrator_no_progress_rounds == 4
    assert settings.orchestrator_rounds_warning_ratio == 0.75


def test_round_budget_config_validation() -> None:
    """UT-76（ADR-058）：新增配置项的边界校验。"""
    import pydantic

    from app.core.config import Settings

    base = {
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "REDIS_URL": "redis://localhost:6379/0",
    }
    # no_progress_rounds 最小 2
    with pytest.raises(pydantic.ValidationError):
        Settings(**base, ORCHESTRATOR_NO_PROGRESS_ROUNDS=1)
    assert Settings(**base, ORCHESTRATOR_NO_PROGRESS_ROUNDS=2).orchestrator_no_progress_rounds == 2
    # 警示比例 ∈ (0, 1]
    for invalid in (0, 1.5):
        with pytest.raises(pydantic.ValidationError):
            Settings(**base, ORCHESTRATOR_ROUNDS_WARNING_RATIO=invalid)
    assert (
        Settings(**base, ORCHESTRATOR_ROUNDS_WARNING_RATIO=0.5).orchestrator_rounds_warning_ratio
        == 0.5
    )


async def test_multi_stage_scouting_not_killed_by_round_limit(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-77（ADR-058 核心守卫）：多阶段侦察**不再**被轮数上限误杀。

    回归对象：真实会话 `895b1003` 中，模型连续 6 轮做「配置服务器→查清单→
    查指引→定位产物」等**各不相同**的侦察调用，被 `max_rounds=8` 强制收尾，
    正文出现「已达到最大技能调用轮数」。
    """
    # 6 个各不相同的真实技能，模拟多阶段侦察
    skills = [
        "bt_mcp_servers",
        "bt_mcp_tools",
        "bt_deploy_guide",
        "bt_mcp_schema",
        "list_standard_docs",
        "find_idle_nodes",
    ]
    monkeypatch.setattr("app.agents.orchestrator.get_settings", _fake_settings(8))
    client = _ScriptedSkillsClient(skills)
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(
        agent.chat_stream([{"role": "user", "content": "部署这个项目"}], mode="fast")
    )

    # 6 轮侦察全部执行完（未被第 8 轮上限拦下），最后一次调用为纯文本收尾
    assert len(client.calls) == 7, f"应执行 6 轮工具 + 1 轮收尾，实际 {len(client.calls)}"
    reply = "".join(e["data"]["delta"] for e in events if e["event"] == "chunk")
    assert "最大技能调用轮数" not in reply
    assert "已基于现有信息给出最终回答" in reply
    assert "error" not in [e["event"] for e in events]


async def test_no_progress_stops_with_distinct_message(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UT-78（ADR-058 D2）：换参数反复查同一批工具 → 按「无新信息」收尾。"""
    monkeypatch.setattr(
        "app.agents.orchestrator.get_settings",
        _fake_settings(40, orchestrator_no_progress_rounds=2),
    )
    # 同一技能反复调用、但**参数逐轮不同**（签名不同 ⇒ 不会触发 Doom Loop），
    # 这正是 ADR-058 要覆盖而 Doom Loop 覆盖不到的形态
    client = _ScriptedSkillsClient(["bt_mcp_tools"], repeat=True)
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    with caplog.at_level("WARNING"):
        events = await _collect(
            agent.chat_stream([{"role": "user", "content": "继续"}], mode="fast")
        )

    reply = "".join(e["data"]["delta"] for e in events if e["event"] == "chunk")
    # 收尾文案必须是「无新信息」而非「轮数上限」
    assert "最大技能调用轮数" not in reply
    warnings = [e["data"]["message"] for e in events if e["event"] == "warning"]
    assert any("没有获得新信息" in message for message in warnings), warnings
    # 强不变量：远早于 40 轮上限就停（而非烧满上限）
    assert len(client.calls) <= 5, f"应在极少数轮内因无进展收尾，实际 {len(client.calls)}"
    # 日志记录了真实收尾原因
    assert any(
        getattr(record, "event", None) == "orchestrator_no_progress_stop"
        for record in caplog.records
    )
    assert "已基于现有信息给出最终回答" in reply


async def test_new_skill_resets_no_progress_streak(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-79（ADR-058）：出现新技能即清零无进展计数。"""
    monkeypatch.setattr(
        "app.agents.orchestrator.get_settings",
        _fake_settings(40, orchestrator_no_progress_rounds=2),
    )
    # A → A（无进展 1）→ B（清零）→ B（无进展 1）→ C（清零）→ 收尾
    client = _ScriptedSkillsClient(
        ["bt_mcp_tools", "bt_mcp_tools", "bt_mcp_servers", "bt_mcp_servers", "find_idle_nodes"]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "继续"}], mode="fast"))

    # 5 轮工具 + 1 轮收尾：中途没有触发无进展收尾
    assert len(client.calls) == 6
    reply = "".join(e["data"]["delta"] for e in events if e["event"] == "chunk")
    assert "已基于现有信息给出最终回答" in reply


async def test_failed_skill_counts_as_returned(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-80（ADR-058）：技能失败也算「已返回」，不累积无进展。"""
    monkeypatch.setattr(
        "app.agents.orchestrator.get_settings",
        _fake_settings(40, orchestrator_no_progress_rounds=2),
    )
    # 每轮都是新技能（即使其中部分技能会失败：如 bt_mcp_schema 缺参）
    client = _ScriptedSkillsClient(
        ["bt_mcp_schema", "history_recall", "find_idle_nodes", "bt_mcp_servers"]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "继续"}], mode="fast"))

    assert len(client.calls) == 5  # 4 轮工具 + 收尾，未触发无进展
    failed_steps = [
        e for e in events if e["event"] == "step_finished" and e["data"]["status"] == "failed"
    ]
    assert failed_steps, "用例前提：应有技能失败（失败也计入已返回）"


async def test_rounds_warning_emitted_once(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-81（ADR-058 D3）：逼近上限时下发一次警示，且不重复。"""
    monkeypatch.setattr(
        "app.agents.orchestrator.get_settings",
        _fake_settings(4, orchestrator_rounds_warning_ratio=0.75),
    )
    # 4 轮工具（每轮新技能）→ 第 3 轮触发警示（ceil(4×0.75)=3）
    client = _ScriptedSkillsClient(
        ["bt_mcp_servers", "bt_mcp_tools", "find_idle_nodes", "bt_mcp_servers"]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "继续"}], mode="fast"))

    warnings = [
        e["data"]["message"]
        for e in events
        if e["event"] == "warning" and "已使用" in str(e["data"].get("message", ""))
    ]
    assert len(warnings) == 1, f"警示应只出现一次，实际 {warnings}"


async def test_max_rounds_keeps_triple_disable_semantics(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-82（ADR-058 守卫）：达到上限仍是三重禁用收尾（回归 ADR-040）。"""
    monkeypatch.setattr("app.agents.orchestrator.get_settings", _fake_settings(3))
    client = _ScriptedSkillsClient(
        ["bt_mcp_servers", "bt_mcp_tools", "find_idle_nodes"], repeat=True
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "继续"}], mode="fast"))

    # 最后一次调用：无 tools 且 tool_choice="none"
    assert client.calls[-1]["tools"] is None
    assert client.calls[-1]["tool_choice"] == "none"
    assert any(
        "最大技能调用轮数" in str(m.get("content") or "") for m in client.calls[-1]["messages"]
    )
    assert events[-1]["event"] == "done"


async def test_doom_loop_still_triggers(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-83（ADR-058 守卫）：Doom Loop（连续 3 轮同名同参）行为不变。"""

    class _IdenticalClient(FakeDeepSeekClient):
        async def chat_completion_stream_raw(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            tool_choice: str | None = None,
            reasoning: bool = False,
            thinking: bool | None = None,
            max_tokens: int | None = None,
        ) -> AsyncGenerator[dict[str, Any], None]:
            self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
            self._cursor += 1
            if tools:
                yield {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "same",
                                "type": "function",
                                "function": {"name": "find_idle_nodes", "arguments": "{}"},
                            }
                        ],
                    },
                }
                return
            yield {"type": "message", "message": {"role": "assistant", "content": "收尾"}}

    monkeypatch.setattr("app.agents.orchestrator.get_settings", _fake_settings(40))
    client = _IdenticalClient([])
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    warnings = [e["data"]["message"] for e in events if e["event"] == "warning"]
    assert any("重复的工具调用" in message for message in warnings), warnings


async def test_skill_timeout_still_continues(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-84（ADR-058 守卫）：技能超时后循环继续，且不误触无进展收尾（回归 ADR-043）。"""
    from app.skills.base import SkillResult

    class _HangingSkill:
        name = "hanging_skill"
        display_name = "挂起技能"

        async def execute(self, session: AsyncSession, context: Any):  # type: ignore[no-untyped-def]
            await asyncio.sleep(10)
            yield {  # pragma: no cover - 超时后不可达
                "event": "_result",
                "data": SkillResult(success=True, summary="不应到达"),
            }

    monkeypatch.setattr(
        "app.agents.orchestrator.find_skill",
        lambda key: _HangingSkill() if key == "hanging_skill" else None,
    )
    monkeypatch.setattr(
        "app.agents.orchestrator.get_settings",
        _fake_settings(40, skill_execution_timeout_seconds=0.05),
    )
    client = FakeDeepSeekClient(
        [
            {
                "content": None,
                "tool_calls": [
                    {"id": "call-t", "function": {"name": "hanging_skill", "arguments": "{}"}}
                ],
            },
            {"content": "技能超时后仍能回答", "tool_calls": None},
        ]
    )
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "hi"}], mode="fast"))

    finished = next(e for e in events if e["event"] == "step_finished")["data"]
    assert finished["status"] == "failed"
    assert "超时" in finished["result_summary"]
    assert events[-1]["event"] == "done"
    # 超时是首次调用 → 计为「已返回」，不得触发无进展收尾
    reply = "".join(e["data"]["delta"] for e in events if e["event"] == "chunk")
    assert "技能超时后仍能回答" in reply


async def test_no_progress_takes_precedence_over_round_limit(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-85（ADR-058）：同轮满足两者时，以「无新信息」解释收尾原因。"""
    # max_rounds=3 且无进展阈值 2：轮 1 新技能、轮 2 无进展（=1）、轮 3 达阈值收尾
    # —— 停在 no_progress 分支而非循环退出后的 max_rounds 分支
    monkeypatch.setattr(
        "app.agents.orchestrator.get_settings",
        _fake_settings(3, orchestrator_no_progress_rounds=2),
    )
    client = _ScriptedSkillsClient(["bt_mcp_servers"], repeat=True)
    agent = OrchestratorAgent(db_session, deepseek_client=client)  # type: ignore[arg-type]
    events = await _collect(agent.chat_stream([{"role": "user", "content": "继续"}], mode="fast"))

    reply = "".join(e["data"]["delta"] for e in events if e["event"] == "chunk")
    assert "最大技能调用轮数" not in reply
    warnings = [e["data"]["message"] for e in events if e["event"] == "warning"]
    assert any("没有获得新信息" in message for message in warnings), warnings
