"""DeepSeek API 客户端封装。"""

import asyncio
import json
import logging
import random
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, TypeVar

import httpx

from app.core.config import get_settings
from app.models.base import AsyncSessionLocal
from app.models.llm_usage_record import LlmUsageRecord
from app.services.llm_provider_service import LLMProviderConfig

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_TEMPERATURE = 0.3
# 显式抬高输出上限：DeepSeek 默认 max_tokens 偏小（4K），三件套长 JSON
# 会被截断导致 json.loads 失败（表现为「AI 生成规范格式错误」）。
#
# ADR-056（P2）：该常量降级为「兜底默认档」。模型（deepseek-flash）官方
# MAX OUTPUT = 384K，旧值 8192 已实测触顶 2 次（长回答被静默截断）。
# 运行期默认档改用 settings.llm_max_output_tokens（32768），并按场景降档。
MAX_OUTPUT_TOKENS = 8192
# 摘要（上下文压缩检查点）输出档：五段摘要，固定上限足够
SUMMARY_MAX_OUTPUT_TOKENS = 4096
# 三件套/规范文档生成档：单文档直出，过长无益
SPEC_BUILDER_MAX_OUTPUT_TOKENS = 16384
MAX_ERROR_SNIPPET_LENGTH = 100

# 可重试的 API 状态码（ADR-044）：限流与服务端瞬时故障；
# 其余 4xx（参数/鉴权类）重试无意义，直接抛出
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

_T = TypeVar("_T")

#: 后台用量落库任务引用集（防止 create_task 结果被 GC，ADR-038）
_usage_tasks: set[asyncio.Task[None]] = set()


def _is_retryable_error(exc: BaseException) -> bool:
    """判定异常是否可重试（ADR-044）：限流/服务端瞬时故障/网络层异常。"""
    if isinstance(exc, DeepSeekAPIError):
        return exc.status_code in RETRYABLE_STATUS_CODES
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException, TimeoutError))


class DeepSeekAPIError(Exception):
    """DeepSeek API 调用异常。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"DeepSeek API 错误 {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class DeepSeekClient:
    """异步 DeepSeek API 客户端。"""

    def __init__(
        self,
        config: LLMProviderConfig,
        async_client: httpx.AsyncClient | None = None,
        scene: str = "hub",
    ) -> None:
        """初始化客户端。

        Args:
            config: LLM Provider 运行时配置。
            async_client: 外部传入的 httpx 异步客户端，用于测试或连接池复用。
            scene: 调用场景标签（planning/orchestrator/spec_chat/ai_dispatch），
                随 token 用量记录落库（ADR-038）。
        """
        self._config = config
        self._scene = scene
        self._validate_config()
        # 多模型兼容（ADR-030）：thinking / reasoning_effort 是 DeepSeek 私有
        # API 扩展，OpenAI 兼容端点（Ollama/vLLM/one-api 等）会因未知参数拒绝
        # 请求。按 base_url 判定：仅 DeepSeek 官方端点下发专属参数。
        self._deepseek_compatible = "deepseek" in (config.base_url or "").lower()

        if async_client is not None:
            self._client = async_client
        else:
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                headers={"Authorization": f"Bearer {self._config.api_key}"},
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )

    def _validate_config(self) -> None:
        """校验 DeepSeek 相关配置是否完整。"""
        if not self._config.api_key:
            raise ValueError("DeepSeek API Key 未配置")
        if not self._config.base_url:
            raise ValueError("DeepSeek Base URL 未配置")
        if not self._config.model:
            raise ValueError("DeepSeek Model 未配置")

    async def _sleep_backoff(self, failed_attempt: int) -> None:
        """第 failed_attempt 次尝试失败后的指数退避睡眠（ADR-044）。

        base×2^(failed_attempt-1)，±20% 抖动；退避序列 1s/2s/4s/8s（基数 1s）。
        """
        settings = get_settings()
        delay = settings.llm_retry_base_delay_seconds * (2 ** (failed_attempt - 1))
        await asyncio.sleep(delay * random.uniform(0.8, 1.2))

    def _log_retry(self, attempt: int, exc: BaseException) -> None:
        """记录重试日志（ADR-044）。"""
        logger.warning(
            "llm_request_retry",
            extra={
                "event": "llm_request_retry",
                "scene": self._scene,
                "model": self._config.model,
                "attempt": attempt,
                "error": str(exc)[:MAX_ERROR_SNIPPET_LENGTH],
            },
        )

    async def _with_retries(self, send: Callable[[], Awaitable[_T]]) -> _T:
        """非流式请求重试包装（ADR-044）。

        可重试错误（限流/5xx/网络异常/超时）按配置指数退避重试，
        最多 ``llm_retry_max_attempts`` 次尝试；其余错误立即抛出。
        """
        attempts = max(1, get_settings().llm_retry_max_attempts)
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                await self._sleep_backoff(attempt - 1)
            try:
                return await send()
            except Exception as exc:
                if not _is_retryable_error(exc) or attempt >= attempts:
                    raise
                self._log_retry(attempt, exc)
        raise RuntimeError("unreachable: retry loop must return or raise")  # pragma: no cover

    def _record_usage(self, usage: dict[str, Any] | None) -> None:
        """异步落库 token 用量（ADR-038）与 cache 命中观测（ADR-044）。

        后台 fire-and-forget：任何写库失败仅告警，绝不影响 LLM 调用主链路。
        """
        if not usage:
            return
        cache_hit = int(usage.get("prompt_cache_hit_tokens") or 0)
        cache_miss = int(usage.get("prompt_cache_miss_tokens") or 0)
        payload = {
            "source": "hub",
            "scene": self._scene,
            "model": self._config.model,
            "prompt_tokens": usage.get("prompt_tokens") or 0,
            "completion_tokens": usage.get("completion_tokens") or 0,
            "total_tokens": usage.get("total_tokens") or 0,
            "prompt_cache_hit_tokens": cache_hit,
            "prompt_cache_miss_tokens": cache_miss,
        }
        if cache_hit + cache_miss > 0:
            logger.debug(
                "llm_prompt_cache_stats",
                extra={
                    "event": "llm_prompt_cache_stats",
                    "scene": self._scene,
                    "model": self._config.model,
                    "hit_tokens": cache_hit,
                    "miss_tokens": cache_miss,
                    "hit_ratio": round(cache_hit / (cache_hit + cache_miss), 4),
                },
            )

        async def _insert() -> None:
            try:
                async with AsyncSessionLocal() as session:
                    session.add(LlmUsageRecord(**payload))
                    await session.commit()
            except Exception:
                logger.warning(
                    "llm_usage_record_failed",
                    extra={
                        "event": "llm_usage_record_failed",
                        "scene": self._scene,
                        "model": self._config.model,
                    },
                    exc_info=True,
                )

        task = asyncio.create_task(_insert())
        _usage_tasks.add(task)
        task.add_done_callback(_usage_tasks.discard)

    async def chat_completion(self, system_prompt: str, user_prompt: str) -> str:
        """调用 DeepSeek /chat/completions 接口。

        Args:
            system_prompt: 系统提示词。
            user_prompt: 用户提示词。

        Returns:
            DeepSeek 返回的文本内容。

        Raises:
            DeepSeekAPIError: 当 API 返回非 2xx 响应时抛出。
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return await self.chat_completion_messages(messages)

    async def chat_completion_raw(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, str] | None = None,
        reasoning: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """调用 DeepSeek /chat/completions 并返回原始 assistant message。

        与 :meth:`chat_completion_messages` 的区别：保留完整响应结构
        （``content`` / ``tool_calls`` / ``reasoning_content``），
        供 function calling 循环（ADR-033 Orchestrator）使用。

        Args:
            messages: 符合 OpenAI 格式的消息列表。
            response_format: 可选的响应格式约束。
            reasoning: 是否开启深度思考。
            tools: 可选的 function calling 工具定义（OpenAI tools 格式）。

        Returns:
            choices[0].message 字典。

        Raises:
            DeepSeekAPIError: 当 API 返回非 2xx 响应时抛出。
        """
        payload = self._build_payload(
            messages, response_format=response_format, reasoning=reasoning
        )
        if tools:
            payload["tools"] = tools
        logger.debug(
            "deepseek_request_started",
            extra={
                "event": "deepseek_request_started",
                "model": self._config.model,
                "base_url": self._config.base_url,
                "reasoning": reasoning,
                "tools_count": len(tools or []),
            },
        )

        async def _send_once() -> httpx.Response:
            # 单次尝试 = 发请求 + 状态码检查（4xx/5xx 在重试单元内抛出，
            # 可重试状态码才能触发退避重试，ADR-044）
            resp = await self._client.post("/chat/completions", json=payload)
            if resp.status_code >= 400:
                error_snippet = resp.text[:MAX_ERROR_SNIPPET_LENGTH]
                logger.warning(
                    "deepseek_request_failed",
                    extra={
                        "event": "deepseek_request_failed",
                        "status_code": resp.status_code,
                        "error_snippet": error_snippet,
                    },
                )
                raise DeepSeekAPIError(status_code=resp.status_code, message=resp.text)
            return resp

        response = await self._with_retries(_send_once)
        data = response.json()
        message = data["choices"][0]["message"]
        self._record_usage(data.get("usage"))
        logger.debug(
            "deepseek_request_completed",
            extra={
                "event": "deepseek_request_completed",
                "has_tool_calls": bool(message.get("tool_calls")),
            },
        )
        return message

    async def chat_completion_messages(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
        reasoning: bool = False,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        thinking: bool | None = None,
        temperature: float | None = None,
    ) -> str:
        """调用 DeepSeek /chat/completions 接口（自定义 messages）。

        Args:
            messages: 符合 OpenAI 格式的消息列表。
            response_format: 可选的响应格式约束，如 ``{"type": "json_object"}``
                启用 DeepSeek JSON Mode，强制模型输出合法 JSON。
            reasoning: 是否开启深度思考（旧语义，``thinking is None`` 时生效）。
            tools: 可选的 function calling 工具定义。仅供**前缀对齐**用途
                （ADR-054 摘要热前缀复用：带 tools 的辅助调用才能与上一次真实
                请求逐字节共享前缀，从而命中 provider KV 缓存）；缺省不传，
                行为与既有调用完全一致。
            max_tokens: 输出上限档位（ADR-056）；缺省取配置默认档。
            thinking: 思考模式开关（ADR-056 D3）；``None`` 保持旧行为。
            temperature: 采样温度（非思考模式生效）；缺省取默认值。

        Returns:
            DeepSeek 返回的文本内容。

        Raises:
            DeepSeekAPIError: 当 API 返回非 2xx 响应时抛出。
        """
        payload = self._build_payload(
            messages,
            response_format=response_format,
            reasoning=reasoning,
            thinking=thinking,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if tools:
            payload["tools"] = tools

        logger.debug(
            "deepseek_request_started",
            extra={
                "event": "deepseek_request_started",
                "model": self._config.model,
                "base_url": self._config.base_url,
                "reasoning": reasoning,
                "tools_count": len(tools or []),
            },
        )

        async def _send_once() -> httpx.Response:
            # 单次尝试 = 发请求 + 状态码检查（同 chat_completion_raw，ADR-044）
            resp = await self._client.post("/chat/completions", json=payload)
            if resp.status_code >= 400:
                error_snippet = resp.text[:MAX_ERROR_SNIPPET_LENGTH]
                logger.warning(
                    "deepseek_request_failed",
                    extra={
                        "event": "deepseek_request_failed",
                        "status_code": resp.status_code,
                        "error_snippet": error_snippet,
                    },
                )
                raise DeepSeekAPIError(status_code=resp.status_code, message=resp.text)
            return resp

        response = await self._with_retries(_send_once)

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        self._record_usage(data.get("usage"))
        # ADR-056 决策 D2：输出触顶告警（非流式路径同样覆盖）
        self._check_output_truncated(data.get("usage"), int(payload["max_tokens"]))
        logger.debug(
            "deepseek_request_completed",
            extra={
                "event": "deepseek_request_completed",
                "content_length": len(content),
            },
        )
        return content

    async def chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
        reasoning: bool = False,
    ) -> AsyncGenerator[dict[str, str | None], None]:
        """调用 DeepSeek /chat/completions 接口并流式返回内容。

        Args:
            messages: 符合 OpenAI 格式的消息列表。
            response_format: 可选的响应格式约束。
            reasoning: 是否开启深度思考。

        Yields:
            DeepSeek 返回的文本增量，字典包含 ``content``（正式回复）和
            ``reasoning``（深度思考过程）两个字段，未返回时值为 ``None``。

        Raises:
            DeepSeekAPIError: 当 API 返回非 2xx 响应时抛出。
        """
        payload = self._build_payload(
            messages,
            response_format=response_format,
            reasoning=reasoning,
            stream=True,
        )
        logger.debug(
            "deepseek_stream_request_started",
            extra={
                "event": "deepseek_stream_request_started",
                "model": self._config.model,
                "base_url": self._config.base_url,
                "reasoning": reasoning,
            },
        )
        # 流式重试（ADR-044）：仅首个增量 yield 前的失败可重试（连接建立/
        # 状态码检查阶段）；首帧下发后流中断直接抛出——前端已收到部分内容，
        # 重建流会导致重复输出
        attempts = max(1, get_settings().llm_retry_max_attempts)
        attempt = 0
        while True:
            attempt += 1
            emitted = False
            try:
                async with self._client.stream(
                    "POST", "/chat/completions", json=payload
                ) as response:
                    if response.status_code >= 400:
                        await self._raise_stream_error(response)
                    async for chunk in self._parse_sse_lines(response):
                        emitted = True
                        yield chunk
                logger.debug(
                    "deepseek_stream_request_completed",
                    extra={"event": "deepseek_stream_request_completed"},
                )
                return
            except Exception as exc:
                if emitted or not _is_retryable_error(exc) or attempt >= attempts:
                    raise
                self._log_retry(attempt, exc)
                await self._sleep_backoff(attempt)

    async def chat_completion_stream_raw(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        reasoning: bool = False,
        thinking: bool | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式调用 /chat/completions 并产出结构化增量事件（ADR-040）。

        支持 function calling：``delta.tool_calls`` 增量按 ``index`` 拼装
        （首帧携带 id/name，后续帧仅 arguments 分片），供 Orchestrator
        多轮循环真流式使用。

        Args:
            thinking: 思考模式开关（ADR-056 D3）；``None`` 保持旧行为。
            max_tokens: 输出上限档位；缺省取配置默认档。

        Yields:
            事件字典序列：
            - ``{"type": "reasoning_delta", "delta": str}`` 深度思考增量；
            - ``{"type": "content_delta", "delta": str}`` 正文增量；
            - ``{"type": "message", "message": dict, "usage": dict | None}``
              流结束时的完整 assistant message（含拼装后的 tool_calls，
              OpenAI 原格式）与真实 usage（prompt_tokens 用于 ADR-041
              上下文占用展示）。

        Raises:
            DeepSeekAPIError: 当 API 返回非 2xx 响应时抛出。
        """
        payload = self._build_payload(
            messages, reasoning=reasoning, stream=True, thinking=thinking, max_tokens=max_tokens
        )
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        logger.debug(
            "deepseek_stream_raw_request_started",
            extra={
                "event": "deepseek_stream_raw_request_started",
                "model": self._config.model,
                "tools_count": len(tools or []),
                "tool_choice": tool_choice,
            },
        )
        # 流式重试（ADR-044）：仅首个增量（reasoning/content）yield 前的失败
        # 可重试——tool_calls 增量仅拼装不产出，重试安全；首帧下发后流中断
        # 直接抛出（前端已收到部分内容，重建流会重复）。拼装缓冲随重试重置。
        attempts = max(1, get_settings().llm_retry_max_attempts)
        attempt = 0
        while True:
            attempt += 1
            emitted = False
            content_parts: list[str] = []
            tool_calls_by_index: dict[int, dict[str, Any]] = {}
            usage: dict[str, Any] | None = None
            try:
                async with self._client.stream(
                    "POST", "/chat/completions", json=payload
                ) as response:
                    if response.status_code >= 400:
                        await self._raise_stream_error(response)
                    async for line in response.aiter_lines():
                        if not line or line.startswith(":"):
                            continue
                        if line == "data: [DONE]":
                            break
                        if not line.startswith("data: "):
                            continue
                        try:
                            data = json.loads(line[len("data: ") :])
                        except json.JSONDecodeError:
                            continue
                        if data.get("usage"):
                            usage = data["usage"]
                        choices = data.get("choices") or []
                        delta = choices[0].get("delta", {}) if choices else {}
                        reasoning_delta = delta.get("reasoning_content")
                        if reasoning_delta:
                            emitted = True
                            yield {"type": "reasoning_delta", "delta": reasoning_delta}
                        content_delta = delta.get("content")
                        if content_delta:
                            emitted = True
                            content_parts.append(content_delta)
                            yield {"type": "content_delta", "delta": content_delta}
                        for tc in delta.get("tool_calls") or []:
                            index = int(tc.get("index") or 0)
                            entry = tool_calls_by_index.setdefault(
                                index,
                                {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                },
                            )
                            if tc.get("id"):
                                entry["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                entry["function"]["name"] = fn["name"]
                            if fn.get("arguments"):
                                entry["function"]["arguments"] += fn["arguments"]
                break
            except Exception as exc:
                if emitted or not _is_retryable_error(exc) or attempt >= attempts:
                    raise
                self._log_retry(attempt, exc)
                await self._sleep_backoff(attempt)
        self._record_usage(usage)
        self._check_output_truncated(usage, int(payload["max_tokens"]))
        tool_calls = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index)]
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts) or None,
        }
        if tool_calls:
            message["tool_calls"] = tool_calls
        logger.debug(
            "deepseek_stream_raw_request_completed",
            extra={
                "event": "deepseek_stream_raw_request_completed",
                "has_tool_calls": bool(tool_calls),
                "content_length": len("".join(content_parts)),
            },
        )
        yield {"type": "message", "message": message, "usage": usage}

    async def _raise_stream_error(self, response: httpx.Response) -> None:
        """读取流式错误响应并抛出 DeepSeekAPIError。"""
        error_snippet = (await response.aread())[:MAX_ERROR_SNIPPET_LENGTH].decode(
            "utf-8", errors="replace"
        )
        logger.warning(
            "deepseek_stream_request_failed",
            extra={
                "event": "deepseek_stream_request_failed",
                "status_code": response.status_code,
                "error_snippet": error_snippet,
            },
        )
        raise DeepSeekAPIError(
            status_code=response.status_code,
            message=error_snippet,
        )

    async def _parse_sse_lines(
        self,
        response: httpx.Response,
    ) -> AsyncGenerator[dict[str, str | None], None]:
        """解析 SSE 行并产出内容增量。"""
        usage: dict[str, Any] | None = None
        async for line in response.aiter_lines():
            if not line or line.startswith(":"):
                continue
            if line == "data: [DONE]":
                break
            if not line.startswith("data: "):
                continue
            data_str = line[len("data: ") :]
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            # stream_options.include_usage 的末帧携带 usage（choices 为空）
            if data.get("usage"):
                usage = data["usage"]
            choices = data.get("choices") or []
            delta = choices[0].get("delta", {}) if choices else {}
            content = delta.get("content")
            reasoning_content = delta.get("reasoning_content")
            if content or reasoning_content:
                yield {"content": content, "reasoning": reasoning_content}
        self._record_usage(usage)

    def _build_payload(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
        reasoning: bool = False,
        stream: bool = False,
        thinking: bool | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict:
        """构建 DeepSeek 请求 payload（ADR-056 参数矩阵）。

        Args:
            reasoning: 旧语义（ADR-044 前）——仅在 ``thinking is None`` 时生效：
                True 表示下发 ``reasoning_effort=medium``（不显式声明 thinking 开关）。
            thinking: 思考模式开关（ADR-056 决策 D3）。``None`` 保持旧行为；
                True → 下发 ``thinking={"type":"enabled"}`` + ``reasoning_effort``，
                **不下发 temperature**（官方：思考模式不支持 temperature）；
                False → 下发 ``thinking={"type":"disabled"}``，不下发 reasoning_effort。
            max_tokens: 输出上限档位；缺省取 ``settings.llm_max_output_tokens``。
            temperature: 采样温度；缺省取 :data:`DEFAULT_TEMPERATURE`
                （思考模式下忽略该参数并不下发）。
        """
        payload: dict = {
            "model": self._config.model,
            "messages": messages,
            "max_tokens": max_tokens or get_settings().llm_max_output_tokens,
            "stream": stream,
        }
        if not self._deepseek_compatible:
            # OpenAI 兼容端点：只发标准参数（思考开关/reasoning_effort 为
            # DeepSeek 私有扩展，第三方端点会 400；思考流字段缺失无害）
            payload["temperature"] = DEFAULT_TEMPERATURE if temperature is None else temperature
            if response_format is not None:
                payload["response_format"] = response_format
            if stream:
                payload["stream_options"] = {"include_usage": True}
            return payload
        if thinking is True:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = get_settings().llm_reasoning_effort
        elif thinking is False:
            payload["thinking"] = {"type": "disabled"}
            payload["temperature"] = DEFAULT_TEMPERATURE if temperature is None else temperature
        else:
            # 旧行为（向后兼容）：不显式声明 thinking 开关
            payload["temperature"] = DEFAULT_TEMPERATURE if temperature is None else temperature
            if reasoning:
                payload["reasoning_effort"] = "medium"
        if response_format is not None:
            payload["response_format"] = response_format
        if stream:
            # ADR-038：末帧带回 usage，用于 token 用量统计
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _check_output_truncated(self, usage: dict[str, Any] | None, max_tokens: int) -> None:
        """输出触顶告警（ADR-056 决策 D2）：截断从静默变为可观测。"""
        if not usage:
            return
        completion_tokens = int(usage.get("completion_tokens") or 0)
        if completion_tokens >= max_tokens > 0:
            logger.warning(
                "llm_output_truncated",
                extra={
                    "event": "llm_output_truncated",
                    "scene": self._scene,
                    "model": self._config.model,
                    "max_tokens": max_tokens,
                    "completion_tokens": completion_tokens,
                    "prompt_tokens": usage.get("prompt_tokens"),
                },
            )

    async def close(self) -> None:
        """关闭 HTTP 客户端连接。"""
        await self._client.aclose()

    async def __aenter__(self) -> "DeepSeekClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
