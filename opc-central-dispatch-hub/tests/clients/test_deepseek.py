"""DeepSeek 客户端单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.clients import deepseek as deepseek_module
from app.clients.deepseek import DeepSeekAPIError, DeepSeekClient
from app.services.llm_provider_service import LLMProviderConfig


@pytest.fixture
def valid_config() -> LLMProviderConfig:
    """提供一份完整的测试配置。"""
    return LLMProviderConfig(
        provider="deepseek",
        api_key="sk-test-key",
        base_url="https://api.test.deepseek.com",
        model="deepseek-test",
    )


@pytest.fixture
def mock_http_client() -> MagicMock:
    """提供模拟的 httpx.AsyncClient。"""
    client = MagicMock()
    client.aclose = AsyncMock()
    client.post = AsyncMock()
    return client


@pytest.fixture
def usage_db_recorder(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """替换用量落库的 AsyncSessionLocal 为 mock 工厂，返回 mock 会话。"""
    session = AsyncMock()
    session.add = MagicMock()  # add 为同步方法，避免 AsyncMock 产生未等待协程告警
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = session
    factory = MagicMock(return_value=session_ctx)
    monkeypatch.setattr(deepseek_module, "AsyncSessionLocal", factory)
    return session


async def _drain_usage_tasks() -> None:
    """等待 fire-and-forget 的用量落库任务全部完成。"""
    tasks = [t for t in list(deepseek_module._usage_tasks) if not t.done()]
    if tasks:
        await asyncio.gather(*tasks)


async def test_chat_completion_success(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
) -> None:
    """正常调用应返回 DeepSeek 文本响应。"""
    expected_content = "这是一个结构化任务"
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"choices": [{"message": {"content": expected_content}}]}
    mock_http_client.post.return_value = mock_response

    client = DeepSeekClient(
        config=valid_config,
        async_client=mock_http_client,
    )

    result = await client.chat_completion(
        system_prompt="你是一个任务规划助手",
        user_prompt="帮我开发一个官网落地页",
    )

    assert result == expected_content
    mock_http_client.post.assert_awaited_once()
    call_args = mock_http_client.post.call_args
    assert call_args[0][0] == "/chat/completions"
    payload = call_args.kwargs["json"]
    assert payload["model"] == "deepseek-test"
    assert payload["temperature"] == 0.3
    assert payload["messages"] == [
        {"role": "system", "content": "你是一个任务规划助手"},
        {"role": "user", "content": "帮我开发一个官网落地页"},
    ]


async def test_chat_completion_messages_passes_tools(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
) -> None:
    """UT-38（ADR-054）：chat_completion_messages 支持 tools 透传（前缀对齐用）。

    不传 tools 时 payload 不含该键（向后兼容，存量调用方行为不变）。
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"choices": [{"message": {"content": "摘要正文"}}]}
    mock_http_client.post.return_value = mock_response
    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)
    tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    messages = [
        {"role": "system", "content": "真实 system"},
        {"role": "user", "content": "历史"},
        {"role": "user", "content": "压缩指令"},
    ]

    result = await client.chat_completion_messages(messages, tools=tools)

    assert result == "摘要正文"
    payload = mock_http_client.post.call_args.kwargs["json"]
    assert payload["tools"] == tools
    assert payload["messages"] == messages
    # 流式标志未开启（摘要为非流式调用）
    assert payload["stream"] is False

    # 不传 tools：payload 不含该键
    mock_http_client.post.reset_mock()
    await client.chat_completion_messages(messages)
    payload_without = mock_http_client.post.call_args.kwargs["json"]
    assert "tools" not in payload_without


async def test_chat_completion_api_error(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
) -> None:
    """API 返回非 2xx 响应时应抛出 DeepSeekAPIError。"""
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Invalid API key"
    mock_http_client.post.return_value = mock_response

    client = DeepSeekClient(
        config=valid_config,
        async_client=mock_http_client,
    )

    with pytest.raises(DeepSeekAPIError) as exc_info:
        await client.chat_completion("system", "user")

    assert exc_info.value.status_code == 401
    assert "Invalid API key" in exc_info.value.message


async def test_missing_api_key(valid_config: LLMProviderConfig) -> None:
    """缺少 API Key 时初始化应失败。"""
    invalid_config = LLMProviderConfig(
        provider=valid_config.provider,
        api_key="",
        base_url=valid_config.base_url,
        model=valid_config.model,
    )

    with pytest.raises(ValueError, match="API Key"):
        DeepSeekClient(config=invalid_config)


async def test_close_client(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
) -> None:
    """close 方法应正确关闭底层 HTTP 客户端。"""
    client = DeepSeekClient(
        config=valid_config,
        async_client=mock_http_client,
    )

    await client.close()

    mock_http_client.aclose.assert_awaited_once()


async def test_async_context_manager(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
) -> None:
    """异步上下文管理器应在退出时关闭客户端。"""
    async with DeepSeekClient(
        config=valid_config,
        async_client=mock_http_client,
    ):
        pass

    mock_http_client.aclose.assert_awaited_once()


async def test_chat_completion_records_usage(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
    usage_db_recorder: AsyncMock,
) -> None:
    """ADR-038：非流式响应携带 usage 时异步落库一条用量记录。"""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    mock_http_client.post.return_value = mock_response

    client = DeepSeekClient(config=valid_config, async_client=mock_http_client, scene="planning")
    await client.chat_completion("system", "user")
    await _drain_usage_tasks()

    usage_db_recorder.add.assert_called_once()
    record = usage_db_recorder.add.call_args[0][0]
    assert record.scene == "planning"
    assert record.model == "deepseek-test"
    assert record.prompt_tokens == 10
    assert record.completion_tokens == 5
    assert record.total_tokens == 15


async def test_chat_completion_without_usage_skips_record(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
    usage_db_recorder: AsyncMock,
) -> None:
    """ADR-038：响应无 usage 时不落库（兼容不返回用量的兼容端点）。"""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    mock_http_client.post.return_value = mock_response

    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)
    await client.chat_completion("system", "user")
    await _drain_usage_tasks()

    usage_db_recorder.add.assert_not_called()


async def test_chat_completion_record_failure_never_breaks_call(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-038：用量落库失败仅告警，不影响主调用返回。"""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    mock_http_client.post.return_value = mock_response

    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock(side_effect=RuntimeError("db down"))
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = session
    monkeypatch.setattr(deepseek_module, "AsyncSessionLocal", MagicMock(return_value=session_ctx))

    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)
    result = await client.chat_completion("system", "user")
    assert result == "ok"
    await _drain_usage_tasks()


async def test_stream_records_usage_from_final_frame(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
    usage_db_recorder: AsyncMock,
) -> None:
    """ADR-038：流式末帧 include_usage 的 usage 被捕获并落库。"""
    lines = [
        'data: {"choices":[{"delta":{"content":"Hi"}}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3,"total_tokens":10}}',
        "data: [DONE]",
    ]
    response = MagicMock()
    response.status_code = 200

    async def _aiter_lines():
        for line in lines:
            yield line

    response.aiter_lines = _aiter_lines
    stream_ctx = AsyncMock()
    stream_ctx.__aenter__.return_value = response
    stream_ctx.__aexit__.return_value = False
    mock_http_client.stream = MagicMock(return_value=stream_ctx)

    client = DeepSeekClient(config=valid_config, async_client=mock_http_client, scene="spec_chat")
    chunks = [
        chunk async for chunk in client.chat_completion_stream([{"role": "user", "content": "hi"}])
    ]
    await _drain_usage_tasks()

    assert chunks == [{"content": "Hi", "reasoning": None}]
    usage_db_recorder.add.assert_called_once()
    record = usage_db_recorder.add.call_args[0][0]
    assert record.scene == "spec_chat"
    assert record.total_tokens == 10
    # stream 请求必须带 stream_options.include_usage
    payload = mock_http_client.stream.call_args.kwargs["json"]
    assert payload["stream_options"] == {"include_usage": True}


# ---------------------------------------------------------------------------
# ADR-044 LLM 客户端韧性：重试退避 + cache 观测
# ---------------------------------------------------------------------------


def _fast_retry(monkeypatch: pytest.MonkeyPatch, attempts: int = 5) -> None:
    """注入零退避重试配置，避免测试真实睡眠。"""
    from app.core.config import get_settings as real_get_settings

    class _Proxy:
        def __getattr__(self, name: str) -> object:
            if name == "llm_retry_base_delay_seconds":
                return 0.0
            if name == "llm_retry_max_attempts":
                return attempts
            return getattr(real_get_settings(), name)

    monkeypatch.setattr("app.clients.deepseek.get_settings", lambda: _Proxy())


def _resp(status: int, body: str = "err") -> MagicMock:
    mock = MagicMock()
    mock.status_code = status
    mock.text = body
    return mock


def _ok_response() -> MagicMock:
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    return mock


async def test_retryable_429_then_success(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-1：429 限流后重试成功，调用 2 次。"""
    _fast_retry(monkeypatch)
    mock_http_client.post = AsyncMock(side_effect=[_resp(429), _ok_response()])

    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)
    result = await client.chat_completion("system", "user")

    assert result == "ok"
    assert mock_http_client.post.call_count == 2


async def test_non_retryable_400_raises_immediately(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-2：400 参数/鉴权类错误不重试，立即抛出。"""
    _fast_retry(monkeypatch)
    mock_http_client.post = AsyncMock(return_value=_resp(400, "bad request"))

    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)
    with pytest.raises(DeepSeekAPIError) as exc_info:
        await client.chat_completion("system", "user")

    assert exc_info.value.status_code == 400
    assert mock_http_client.post.call_count == 1


async def test_retry_exhausted_raises_last_error(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-3：可重试错误耗尽尝试次数后抛最后一次错误。"""
    _fast_retry(monkeypatch, attempts=5)
    mock_http_client.post = AsyncMock(return_value=_resp(503, "still down"))

    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)
    with pytest.raises(DeepSeekAPIError) as exc_info:
        await client.chat_completion("system", "user")

    assert exc_info.value.status_code == 503
    assert mock_http_client.post.call_count == 5


async def test_network_error_retried(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-4：网络层异常（ConnectError）可重试。"""
    _fast_retry(monkeypatch)
    mock_http_client.post = AsyncMock(
        side_effect=[httpx.ConnectError("conn refused"), _ok_response()]
    )

    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)
    result = await client.chat_completion("system", "user")

    assert result == "ok"
    assert mock_http_client.post.call_count == 2


def _stream_stage(
    lines: list[str] | None = None,
    status: int = 200,
    enter_error: Exception | None = None,
    raise_after: Exception | None = None,
) -> dict:
    """构造 stream() 的一个响应阶段。"""
    return {
        "lines": lines or [],
        "status": status,
        "enter_error": enter_error,
        "raise_after": raise_after,
    }


def _make_seq_stream_client(stages: list[dict]) -> MagicMock:
    """stream() 按序返回多个响应阶段的 mock httpx 客户端（重试测试用）。"""
    http = MagicMock()
    http.aclose = AsyncMock()
    stages_iter = iter(stages)

    def _stream(*args: object, **kwargs: object) -> AsyncMock:
        stage = next(stages_iter)
        ctx = AsyncMock()
        if stage["enter_error"] is not None:
            ctx.__aenter__.side_effect = stage["enter_error"]
            ctx.__aexit__.return_value = False
            return ctx
        response = MagicMock()
        response.status_code = stage["status"]
        response.aread = AsyncMock(return_value=b"stream error body")

        async def _aiter_lines():
            for line in stage["lines"]:
                yield line
            if stage["raise_after"] is not None:
                raise stage["raise_after"]

        response.aiter_lines = _aiter_lines
        ctx.__aenter__.return_value = response
        ctx.__aexit__.return_value = False
        return ctx

    http.stream = MagicMock(side_effect=_stream)
    return http


async def test_stream_retry_before_first_frame(
    valid_config: LLMProviderConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-5：流式首帧前的连接失败可重试，重试后完整收到增量。"""
    _fast_retry(monkeypatch)
    http = _make_seq_stream_client(
        [
            _stream_stage(enter_error=httpx.ConnectError("conn refused")),
            _stream_stage(lines=['data: {"choices":[{"delta":{"content":"Hi"}}]}', "data: [DONE]"]),
        ]
    )
    client = DeepSeekClient(config=valid_config, async_client=http)
    chunks = [
        chunk async for chunk in client.chat_completion_stream([{"role": "user", "content": "hi"}])
    ]

    assert chunks == [{"content": "Hi", "reasoning": None}]
    assert http.stream.call_count == 2


async def test_stream_no_retry_after_first_frame(
    valid_config: LLMProviderConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-6：首帧下发后流中断不重试（防内容重复），异常直接冒出。"""
    _fast_retry(monkeypatch)
    http = _make_seq_stream_client(
        [
            _stream_stage(
                lines=['data: {"choices":[{"delta":{"content":"Hi"}}]}'],
                raise_after=httpx.ReadError("broken pipe"),
            ),
            _stream_stage(lines=['data: {"choices":[{"delta":{"content":"dup"}}]}']),
        ]
    )
    client = DeepSeekClient(config=valid_config, async_client=http)

    with pytest.raises(httpx.ReadError):
        _ = [
            chunk
            async for chunk in client.chat_completion_stream([{"role": "user", "content": "hi"}])
        ]

    # 未重建流：前端已收到的内容不会重复
    assert http.stream.call_count == 1


async def test_usage_records_prompt_cache_tokens(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
    usage_db_recorder: AsyncMock,
) -> None:
    """UT-7：usage 携带 prompt_cache_hit/miss_tokens 时随用量落库（ADR-044）。"""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 10,
            "total_tokens": 1010,
            "prompt_cache_hit_tokens": 800,
            "prompt_cache_miss_tokens": 200,
        },
    }
    mock_http_client.post.return_value = mock_response

    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)
    await client.chat_completion("system", "user")
    await _drain_usage_tasks()

    usage_db_recorder.add.assert_called_once()
    record = usage_db_recorder.add.call_args[0][0]
    assert record.prompt_cache_hit_tokens == 800
    assert record.prompt_cache_miss_tokens == 200


# ---------------------------------------------------------------------------
# ADR-056（P2）：参数矩阵、输出档位与触顶告警
# ---------------------------------------------------------------------------


def _payload_of(mock_http_client: MagicMock) -> dict:
    """取最近一次 POST 的 payload。"""
    return mock_http_client.post.call_args.kwargs["json"]


async def _call_messages(
    client: DeepSeekClient,
    mock_http_client: MagicMock,
    **kwargs: object,
) -> dict:
    """发一次 chat_completion_messages 并返回 payload。"""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    mock_http_client.post.return_value = mock_response
    await client.chat_completion_messages(
        [{"role": "user", "content": "hi"}],
        **kwargs,  # type: ignore[arg-type]
    )
    return _payload_of(mock_http_client)


async def test_payload_thinking_enabled(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
) -> None:
    """UT-45：thinking=True → 显式 enabled + reasoning_effort，且**不下发 temperature**。"""
    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)
    payload = await _call_messages(client, mock_http_client, thinking=True)

    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] in {"low", "high", "max"}
    assert "temperature" not in payload


async def test_payload_thinking_disabled(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
) -> None:
    """UT-46：thinking=False → 显式 disabled，无 reasoning_effort，有 temperature。"""
    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)
    payload = await _call_messages(client, mock_http_client, thinking=False)

    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload
    assert payload["temperature"] == 0.3


async def test_payload_thinking_none_keeps_legacy_behavior(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
) -> None:
    """UT-47：thinking=None 保持旧行为（向后兼容守卫）。"""
    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)

    payload = await _call_messages(client, mock_http_client, reasoning=True)
    assert payload["reasoning_effort"] == "medium"
    assert "thinking" not in payload
    assert payload["temperature"] == 0.3

    payload = await _call_messages(client, mock_http_client, reasoning=False)
    assert "reasoning_effort" not in payload
    assert "thinking" not in payload


async def test_payload_max_tokens_tier(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
) -> None:
    """UT-48：max_tokens 显式传入优先；缺省取配置默认档（非旧硬编码 8192）。"""
    from app.clients.deepseek import MAX_OUTPUT_TOKENS
    from app.core.config import get_settings

    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)

    payload = await _call_messages(client, mock_http_client)
    assert payload["max_tokens"] == get_settings().llm_max_output_tokens
    assert payload["max_tokens"] != MAX_OUTPUT_TOKENS

    payload = await _call_messages(client, mock_http_client, max_tokens=4096)
    assert payload["max_tokens"] == 4096


async def test_payload_temperature_override(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
) -> None:
    """UT-49：非思考模式下 temperature 可显式覆盖。"""
    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)
    payload = await _call_messages(client, mock_http_client, thinking=False, temperature=0.7)
    assert payload["temperature"] == 0.7


async def test_stream_payload_follows_thinking_matrix(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
) -> None:
    """UT-50：流式请求同样遵循 thinking 参数矩阵。"""

    class _StreamCtx:
        status_code = 200

        async def __aenter__(self) -> "_StreamCtx":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def aiter_lines(self):  # type: ignore[no-untyped-def]
            yield "data: [DONE]"

        async def aread(self) -> bytes:
            return b""

    mock_http_client.stream = MagicMock(side_effect=lambda *a, **k: _StreamCtx())
    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)

    async for _ in client.chat_completion_stream_raw(
        [{"role": "user", "content": "hi"}], thinking=True
    ):
        pass
    payload = mock_http_client.stream.call_args.kwargs["json"]
    assert payload["thinking"] == {"type": "enabled"}
    assert "temperature" not in payload

    async for _ in client.chat_completion_stream_raw(
        [{"role": "user", "content": "hi"}], thinking=False
    ):
        pass
    payload = mock_http_client.stream.call_args.kwargs["json"]
    assert payload["thinking"] == {"type": "disabled"}
    assert "temperature" in payload


async def test_payload_openai_compatible_endpoint_strips_deepseek_params(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
) -> None:
    """多模型兼容：Ollama 等非 DeepSeek 端点不下发 thinking/reasoning_effort 私有参数。"""
    import dataclasses

    ollama_config = dataclasses.replace(valid_config, base_url="http://localhost:11434/v1")
    client = DeepSeekClient(config=ollama_config, async_client=mock_http_client)

    payload = await _call_messages(client, mock_http_client, thinking=True)

    assert "thinking" not in payload
    assert "reasoning_effort" not in payload
    assert payload["temperature"] == 0.3
    assert payload["model"] == "deepseek-test"


async def test_payload_deepseek_official_keeps_thinking_params(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
) -> None:
    """官方 DeepSeek 端点不受兼容分支影响，thinking 参数照常下发。"""
    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)

    payload = await _call_messages(client, mock_http_client, thinking=True)

    assert payload["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" in payload


def test_output_token_tiers() -> None:
    """UT-51：档位常量——摘要 4096 / 三件套 16384 / 默认取配置。"""
    from app.clients.deepseek import (
        SPEC_BUILDER_MAX_OUTPUT_TOKENS,
        SUMMARY_MAX_OUTPUT_TOKENS,
    )
    from app.core.config import get_settings

    assert SUMMARY_MAX_OUTPUT_TOKENS == 4096
    assert SPEC_BUILDER_MAX_OUTPUT_TOKENS == 16384
    assert get_settings().llm_max_output_tokens == 32768


async def test_output_truncated_warning_on_cap(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UT-52：completion_tokens 达上限 → WARNING llm_output_truncated。"""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 4096, "total_tokens": 4196},
    }
    mock_http_client.post.return_value = mock_response
    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)

    with caplog.at_level("WARNING"):
        await client.chat_completion_messages([{"role": "user", "content": "hi"}], max_tokens=4096)

    assert any(
        getattr(record, "event", None) == "llm_output_truncated" for record in caplog.records
    )


async def test_no_truncation_warning_below_cap(
    valid_config: LLMProviderConfig,
    mock_http_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UT-53：未触顶不告警。"""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
    }
    mock_http_client.post.return_value = mock_response
    client = DeepSeekClient(config=valid_config, async_client=mock_http_client)

    with caplog.at_level("WARNING"):
        await client.chat_completion_messages([{"role": "user", "content": "hi"}], max_tokens=4096)

    assert not any(
        getattr(record, "event", None) == "llm_output_truncated" for record in caplog.records
    )
