"""Spec Builder Agent 单元测试（ADR-046：对话判定 + 三文档串行直出）。"""

from types import SimpleNamespace

import pytest

from app.agents.spec_builder import SpecBuilderAgent
from app.agents.spec_builder_parser import parse_decision
from app.clients.deepseek import DeepSeekAPIError
from app.core.errors import TaskSpecificationError

_REPLY_READY = "信息已足够，我来为你生成三件套 <decision>ready</decision>"
_REPLY_NEED_INFO = "请问目标用户是谁？\n<decision>need_info</decision>"
_DOC_SPEC = "# 任务规范\n\n开发静态登录页面。"
_DOC_TASKS = "# 任务清单\n\n1. 编写 login.html"
_DOC_CHECKLIST = "# 验收清单\n\n- 页面可打开"


def _chunks(*content: str) -> list[dict]:
    """构造单轮流式 chunk 列表。"""
    return [{"content": text, "reasoning": None} for text in content]


def _make_agent(rounds: list[list[dict]]) -> tuple[SpecBuilderAgent, list[list]]:
    """构造注入有状态 mock 客户端的 Agent，记录每轮调用入参。

    Returns:
        (agent, calls)：calls 为每轮 (args, kwargs) 记录，供上下文断言。
    """
    agent = SpecBuilderAgent(session=None)  # type: ignore[arg-type]
    calls: list[list] = []
    state = {"round": 0}

    def stream(*args, **kwargs):
        calls.append([list(args), dict(kwargs)])
        chunks = rounds[state["round"]]
        state["round"] += 1

        async def gen():
            for chunk in chunks:
                yield chunk

        return gen()

    client = SimpleNamespace(  # 轻量桩：仅暴露流式方法与模型名
        _config=SimpleNamespace(model="deepseek-test"),
        chat_completion_stream=stream,
    )
    agent._client = client  # noqa: SLF001 — 测试注入
    return agent, calls


async def _collect(agent: SpecBuilderAgent, **kwargs) -> list[dict]:
    events = []
    async for event in agent.chat_stream(**kwargs):
        events.append(event)
    return events


class TestParseDecision:
    """判定标签解析测试。"""

    def test_ready(self) -> None:
        decision, reply = parse_decision(_REPLY_READY)
        assert decision == "ready"
        assert reply == "信息已足够，我来为你生成三件套"

    def test_need_info(self) -> None:
        decision, reply = parse_decision(_REPLY_NEED_INFO)
        assert decision == "need_info"
        assert "目标用户" in reply

    def test_missing_tag_falls_back_to_need_info(self) -> None:
        """标签缺失兜底 need_info（安全侧：宁可多追问不误生成）。"""
        decision, reply = parse_decision("请补充更多信息")
        assert decision == "need_info"
        assert reply == "请补充更多信息"

    def test_unknown_value_falls_back_to_need_info(self) -> None:
        decision, _ = parse_decision("好的 <decision>go</decision>")
        assert decision == "need_info"

    def test_think_tags_stripped(self) -> None:
        decision, reply = parse_decision(
            "<think>推理过程</think>开始生成 <decision>ready</decision>"
        )
        assert decision == "ready"
        assert reply == "开始生成"
        assert "推理过程" not in reply


class TestChatStreamReadyPath:
    """成稿路径：判定轮 + 三文档轮真流式。"""

    async def test_full_flow_events(self) -> None:
        agent, _ = _make_agent(
            [
                _chunks(_REPLY_READY),
                _chunks(_DOC_SPEC),
                _chunks(_DOC_TASKS),
                _chunks(_DOC_CHECKLIST),
            ]
        )
        events = await _collect(
            agent,
            messages=[{"role": "user", "content": "开发登录"}],
            current_specification=None,
            mode="fast",
        )
        names = [e["event"] for e in events]

        assert names[0] == "meta"
        # 判定回复剥标签后下发，正文不含 decision 标记
        reply = "".join(e["data"]["delta"] for e in events if e["event"] == "chunk")
        assert reply == "信息已足够，我来为你生成三件套"
        assert "<decision>" not in reply
        # 编写提示 + 三字段 delta
        hint = [e for e in events if e["event"] == "status" and e["data"].get("message")]
        assert hint and hint[0]["data"]["message"] == "正在编写规范文档…"
        deltas = [e for e in events if e["event"] == "specification_delta"]
        joined = dict.fromkeys(("spec", "tasks", "checklist"), "")
        for e in deltas:
            joined[e["data"]["field"]] += e["data"]["delta"]
        assert joined == {
            "spec": _DOC_SPEC,
            "tasks": _DOC_TASKS,
            "checklist": _DOC_CHECKLIST,
        }
        # 全量 specification 校准 + ready 状态 + done 收尾
        full_idx = names.index("specification")
        assert full_idx > names.index("specification_delta")
        full_event = events[full_idx]
        assert full_event["data"]["spec"] == _DOC_SPEC
        ready_events = [
            e
            for e in events
            if e["event"] == "status" and "is_ready_to_create" in e["data"]
        ]
        assert ready_events == [{"event": "status", "data": {"is_ready_to_create": True}}]
        # ready 状态在全量 specification 之后
        assert names.index("status", full_idx) > full_idx
        assert names[-1] == "done"

    async def test_no_need_info_status_on_ready(self) -> None:
        """成稿路径不得出现 is_ready_to_create:false。"""
        agent, _ = _make_agent(
            [_chunks(_REPLY_READY), _chunks(_DOC_SPEC), _chunks(_DOC_TASKS), _chunks(_DOC_CHECKLIST)]
        )
        events = await _collect(
            agent, messages=[{"role": "user", "content": "开发登录"}], mode="thinking"
        )
        assert all(
            e["data"].get("is_ready_to_create") is not False
            for e in events
            if e["event"] == "status"
        )

    async def test_doc_rounds_carry_context(self) -> None:
        """tasks 轮携带 spec 全文、checklist 轮携带 spec+tasks；各轮含草案。"""
        draft = {"spec": "旧 spec", "tasks": "旧 tasks", "checklist": "旧 checklist"}
        agent, calls = _make_agent(
            [
                _chunks(_REPLY_READY),
                _chunks(_DOC_SPEC),
                _chunks(_DOC_TASKS),
                _chunks(_DOC_CHECKLIST),
            ]
        )
        await _collect(
            agent,
            messages=[{"role": "user", "content": "开发登录"}],
            current_specification=draft,
            mode="fast",
        )
        assert len(calls) == 4
        # 各轮均不启用 JSON Mode（ADR-046 核心变更）
        for _args, kwargs in calls:
            assert "response_format" not in kwargs

        def _system(round_index: int) -> str:
            args, _kwargs = calls[round_index]
            return args[0][0]["content"]

        # spec 轮：写作提示词 + 草案，无前序文档
        assert "spec.md" in _system(1)
        assert "旧 spec" in _system(1)
        assert "已生成的" not in _system(1)
        # tasks 轮：携带 spec 全文
        assert "已生成的 spec 文档" in _system(2)
        assert _DOC_SPEC in _system(2)
        assert _DOC_TASKS not in _system(2)
        # checklist 轮：携带 spec + tasks 全文
        assert _DOC_SPEC in _system(3)
        assert _DOC_TASKS in _system(3)


class TestChatStreamNeedInfoPath:
    """追问路径：仅判定轮，无文档生成。"""

    async def test_need_info_flow(self) -> None:
        agent, calls = _make_agent([_chunks(_REPLY_NEED_INFO)])
        events = await _collect(
            agent, messages=[{"role": "user", "content": "做个网站"}], mode="fast"
        )
        names = [e["event"] for e in events]
        reply = "".join(e["data"]["delta"] for e in events if e["event"] == "chunk")
        assert reply == "请问目标用户是谁？"
        assert "specification_delta" not in names
        assert "specification" not in names
        assert {"event": "status", "data": {"is_ready_to_create": False}} in events
        assert names[-1] == "done"
        assert len(calls) == 1

    async def test_missing_decision_tag_falls_back(self) -> None:
        """判定轮输出无标签时兜底 need_info，不进入文档轮。"""
        agent, calls = _make_agent([_chunks("请补充更多信息")])
        events = await _collect(
            agent, messages=[{"role": "user", "content": "做个网站"}], mode="fast"
        )
        assert {"event": "status", "data": {"is_ready_to_create": False}} in events
        assert len(calls) == 1


class TestChatStreamThinking:
    """thinking 模式：reasoning 实时透传且不混入正文。"""

    async def test_thinking_passthrough(self) -> None:
        agent, _ = _make_agent(
            [
                [
                    {"content": "", "reasoning": "分析需求"},
                    {"content": _REPLY_READY, "reasoning": "信息完备"},
                ],
                _chunks(_DOC_SPEC),
                _chunks(_DOC_TASKS),
                _chunks(_DOC_CHECKLIST),
            ]
        )
        events = await _collect(
            agent, messages=[{"role": "user", "content": "开发登录"}], mode="thinking"
        )
        thinking = "".join(e["data"]["delta"] for e in events if e["event"] == "thinking")
        reply = "".join(e["data"]["delta"] for e in events if e["event"] == "chunk")
        assert "分析需求" in thinking
        assert "信息完备" in thinking
        assert "分析需求" not in reply
        assert "推理" not in reply


class TestChatStreamFailures:
    """失败语义：error + done，无半成品全量 specification。"""

    async def test_conversation_round_error(self) -> None:
        agent, calls = _make_agent([])
        # 让判定轮抛 API 错误
        agent._client.chat_completion_stream = (  # noqa: SLF001
            lambda *args, **kwargs: (_ for _ in ()).throw(
                DeepSeekAPIError(503, "service unavailable")
            )
        )
        events = await _collect(
            agent, messages=[{"role": "user", "content": "开发登录"}], mode="fast"
        )
        names = [e["event"] for e in events]
        assert "error" in names and names[-1] == "done"
        assert "specification" not in names
        assert len(calls) == 0

    async def test_doc_round_error(self) -> None:
        """spec 轮失败：error 终止，tasks/checklist 不再调用。"""
        agent, calls = _make_agent([_chunks(_REPLY_READY)])
        original = agent._client.chat_completion_stream  # noqa: SLF001
        state = {"call": 0}

        def stream(*args, **kwargs):
            state["call"] += 1
            if state["call"] == 2:  # 第一文档轮失败
                raise DeepSeekAPIError(500, "internal error")
            return original(*args, **kwargs)

        agent._client.chat_completion_stream = stream  # noqa: SLF001
        events = await _collect(
            agent, messages=[{"role": "user", "content": "开发登录"}], mode="fast"
        )
        names = [e["event"] for e in events]
        assert "error" in names and names[-1] == "done"
        assert "specification" not in names
        assert len(calls) == 1  # 仅判定轮 + 失败的 spec 轮（抛错不记完整轮次）

    async def test_doc_round_partial_delta_no_full_specification(self) -> None:
        """spec 轮已流出部分增量后失败：不产全量 specification。"""
        agent, _ = _make_agent(
            [
                _chunks(_REPLY_READY),
                [  # spec 轮：先流出半段再抛错
                    {"content": "# 任务规范", "reasoning": None},
                ],
            ]
        )
        original = agent._client.chat_completion_stream  # noqa: SLF001
        state = {"call": 0}

        def stream(*args, **kwargs):
            state["call"] += 1
            if state["call"] == 2:
                async def failing_gen():
                    yield {"content": "# 任务规范", "reasoning": None}
                    raise DeepSeekAPIError(500, "stream broken")

                return failing_gen()
            return original(*args, **kwargs)

        agent._client.chat_completion_stream = stream  # noqa: SLF001
        events = await _collect(
            agent, messages=[{"role": "user", "content": "开发登录"}], mode="fast"
        )
        names = [e["event"] for e in events]
        assert "specification_delta" in names
        assert "specification" not in names
        assert names[-1] == "done"


class TestChatNonStreaming:
    """非流式入口兼容（/chat 端点返回结构不变）。"""

    async def test_chat_collects_result(self) -> None:
        agent, _ = _make_agent(
            [
                _chunks(_REPLY_READY),
                _chunks(_DOC_SPEC),
                _chunks(_DOC_TASKS),
                _chunks(_DOC_CHECKLIST),
            ]
        )
        result = await agent.chat(
            messages=[{"role": "user", "content": "开发登录"}],
            current_specification=None,
            mode="thinking",
        )
        assert result["ai_reply"] == "信息已足够，我来为你生成三件套"
        assert result["specification"] == {
            "spec": _DOC_SPEC,
            "tasks": _DOC_TASKS,
            "checklist": _DOC_CHECKLIST,
        }
        assert result["is_ready_to_create"] is True
        assert result["model"] == "deepseek-test"
        assert result["mode"] == "thinking"

    async def test_chat_need_info_result(self) -> None:
        agent, _ = _make_agent([_chunks(_REPLY_NEED_INFO)])
        result = await agent.chat(
            messages=[{"role": "user", "content": "做个网站"}],
            current_specification=None,
            mode="fast",
        )
        assert result["ai_reply"] == "请问目标用户是谁？"
        assert result["specification"] == {"spec": "", "tasks": "", "checklist": ""}
        assert result["is_ready_to_create"] is False

    async def test_chat_error_raises(self) -> None:
        agent, _ = _make_agent([])
        agent._client.chat_completion_stream = (  # noqa: SLF001
            lambda *args, **kwargs: (_ for _ in ()).throw(
                DeepSeekAPIError(503, "down")
            )
        )
        with pytest.raises(TaskSpecificationError):
            await agent.chat(
                messages=[{"role": "user", "content": "开发登录"}],
                current_specification=None,
            )
