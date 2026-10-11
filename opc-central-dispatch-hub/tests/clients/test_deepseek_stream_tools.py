"""DeepSeekClient 流式 function calling 单元测试（ADR-040）。

验证 ``chat_completion_stream_raw`` 的三类增量事件（思考/正文/工具调用）
与 ``delta.tool_calls`` 按 ``index`` 的分片拼装逻辑。
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.clients import deepseek as deepseek_module
from app.clients.deepseek import DeepSeekAPIError, DeepSeekClient
from app.services.llm_provider_service import LLMProviderConfig


@pytest.fixture
def valid_config() -> LLMProviderConfig:
    return LLMProviderConfig(
        provider="deepseek",
        api_key="sk-test-key",
        base_url="https://api.test.deepseek.com",
        model="deepseek-test",
    )


@pytest.fixture
def usage_db_recorder(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """替换用量落库的 AsyncSessionLocal 为 mock 工厂。"""
    session = AsyncMock()
    session.add = MagicMock()
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = session
    factory = MagicMock(return_value=session_ctx)
    monkeypatch.setattr(deepseek_module, "AsyncSessionLocal", factory)
    return session


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}"


def _make_stream_client(lines: list[str]) -> MagicMock:
    """构造 mock httpx 客户端：stream() 返回逐行产出的 SSE 响应。"""
    response = MagicMock()
    response.status_code = 200

    async def _aiter_lines() -> AsyncGenerator[str, None]:
        for line in lines:
            yield line

    response.aiter_lines = _aiter_lines
    ctx = AsyncMock()
    ctx.__aenter__.return_value = response
    http = MagicMock()
    http.stream = MagicMock(return_value=ctx)
    http.aclose = AsyncMock()
    return http


def _content_chunk(text: str) -> str:
    return _sse({"choices": [{"delta": {"content": text}}]})


def _tool_chunk(index: int, **fields: dict) -> str:
    entry: dict = {"index": index}
    entry.update(fields)
    return _sse({"choices": [{"delta": {"tool_calls": [entry]}}]})


async def _collect(gen: AsyncGenerator) -> list[dict]:
    return [event async for event in gen]


async def test_single_tool_call_assembled_from_fragments(
    valid_config: LLMProviderConfig, usage_db_recorder: AsyncMock
) -> None:
    """UT-1：单工具分片拼装——首帧 id/name、后续帧 arguments 增量。"""
    http = _make_stream_client(
        [
            _content_chunk("让我查一下"),
            _tool_chunk(0, id="call_1", type="function", function={"name": "find_idle_nodes"}),
            _tool_chunk(0, function={"arguments": '{"agent_type"'}),
            _tool_chunk(0, function={"arguments": ':"code"}'}),
            "data: [DONE]",
        ]
    )
    client = DeepSeekClient(config=valid_config, async_client=http)
    events = await _collect(
        client.chat_completion_stream_raw([{"role": "user", "content": "hi"}], tools=[{}])
    )

    deltas = [e["delta"] for e in events if e["type"] == "content_delta"]
    assert "".join(deltas) == "让我查一下"
    message = events[-1]["message"]
    assert message["role"] == "assistant"
    assert message["content"] == "让我查一下"
    tool_call = message["tool_calls"][0]
    assert tool_call["id"] == "call_1"
    assert tool_call["function"]["name"] == "find_idle_nodes"
    assert json.loads(tool_call["function"]["arguments"]) == {"agent_type": "code"}


async def test_multiple_tool_calls_interleaved_fragments(
    valid_config: LLMProviderConfig, usage_db_recorder: AsyncMock
) -> None:
    """UT-2：多工具交错分片按 index 独立拼装、按 index 排序输出。"""
    http = _make_stream_client(
        [
            _tool_chunk(1, id="call_b", function={"name": "get_task_status"}),
            _tool_chunk(0, id="call_a", function={"name": "find_idle_nodes"}),
            _tool_chunk(1, function={"arguments": '{"task_id":"1"}'}),
            _tool_chunk(0, function={"arguments": "{}"}),
            "data: [DONE]",
        ]
    )
    client = DeepSeekClient(config=valid_config, async_client=http)
    events = await _collect(
        client.chat_completion_stream_raw([{"role": "user", "content": "hi"}], tools=[{}, {}])
    )

    tool_calls = events[-1]["message"]["tool_calls"]
    assert [tc["id"] for tc in tool_calls] == ["call_a", "call_b"]
    assert json.loads(tool_calls[0]["function"]["arguments"]) == {}
    assert json.loads(tool_calls[1]["function"]["arguments"]) == {"task_id": "1"}


async def test_mixed_stream_emits_all_event_types(
    valid_config: LLMProviderConfig, usage_db_recorder: AsyncMock
) -> None:
    """UT-3：思考/正文/工具调用混合流，三类事件均正确产出。"""
    http = _make_stream_client(
        [
            _sse({"choices": [{"delta": {"reasoning_content": "先思考"}}]}),
            _content_chunk("正文"),
            _tool_chunk(0, id="call_1", function={"name": "find_idle_nodes", "arguments": "{}"}),
            "data: [DONE]",
        ]
    )
    client = DeepSeekClient(config=valid_config, async_client=http)
    events = await _collect(
        client.chat_completion_stream_raw([{"role": "user", "content": "hi"}], tools=[{}])
    )

    assert {"type": "reasoning_delta", "delta": "先思考"} in events
    assert {"type": "content_delta", "delta": "正文"} in events
    assert events[-1]["type"] == "message"


async def test_usage_final_frame_recorded(
    valid_config: LLMProviderConfig, usage_db_recorder: AsyncMock
) -> None:
    """UT-4：include_usage 末帧的 usage 正常落库。"""
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    http = _make_stream_client(
        [
            _content_chunk("ok"),
            _sse({"choices": [], "usage": usage}),
            "data: [DONE]",
        ]
    )
    client = DeepSeekClient(config=valid_config, async_client=http)
    await _collect(client.chat_completion_stream_raw([{"role": "user", "content": "hi"}]))
    await asyncio.gather(*[t for t in list(deepseek_module._usage_tasks) if not t.done()])

    assert usage_db_recorder.add.called
    record = usage_db_recorder.add.call_args[0][0]
    assert record.total_tokens == 15


async def test_stream_error_raises_api_error(
    valid_config: LLMProviderConfig, usage_db_recorder: AsyncMock
) -> None:
    """UT-5：流式 4xx 错误抛 DeepSeekAPIError 且信息截断。"""
    response = MagicMock()
    response.status_code = 429

    async def _aread() -> bytes:
        return b"x" * 5000

    response.aread = _aread
    ctx = AsyncMock()
    ctx.__aenter__.return_value = response
    http = MagicMock()
    http.stream = MagicMock(return_value=ctx)
    http.aclose = AsyncMock()
    client = DeepSeekClient(config=valid_config, async_client=http)

    with pytest.raises(DeepSeekAPIError) as exc_info:
        await _collect(client.chat_completion_stream_raw([{"role": "user", "content": "hi"}]))
    assert exc_info.value.status_code == 429
    assert len(exc_info.value.message) <= 100


async def test_tool_choice_forwarded_in_payload(
    valid_config: LLMProviderConfig, usage_db_recorder: AsyncMock
) -> None:
    """UT-6：tool_choice 透传 payload；无 tools 时不带 tools 键。"""
    http = _make_stream_client([_content_chunk("ok"), "data: [DONE]"])
    client = DeepSeekClient(config=valid_config, async_client=http)
    await _collect(
        client.chat_completion_stream_raw(
            [{"role": "user", "content": "hi"}], tools=None, tool_choice="none"
        )
    )

    call_kwargs = http.stream.call_args.kwargs
    payload = call_kwargs["json"]
    assert payload["tool_choice"] == "none"
    assert "tools" not in payload
    assert payload["stream"] is True
