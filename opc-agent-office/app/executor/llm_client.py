"""OpenAI 兼容 LLM 客户端（ADR-062 工具循环引擎）。

凭据来自中枢派发 payload（ADR-029 ``executor_model``：model/api_key/base_url），
以 httpx 直连 ``{base_url}/chat/completions``，支持 function calling 与
指数退避重试；凭据仅内存使用，不落日志。
"""

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: 不可重试的客户端错误（凭据/参数问题，重试无意义）
_NON_RETRY_STATUS = {400, 401, 403, 404, 422}


class LLMError(Exception):
    """LLM 调用最终失败（重试耗尽或不可重试错误）。"""


class LLMClient:
    """OpenAI 兼容 Chat Completions 客户端。"""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        self._model = model
        self._api_key = api_key
        base = base_url.rstrip("/")
        # 兼容用户把 base_url 填到 /v1 或填到完整 /chat/completions 的情况
        if base.endswith("/chat/completions"):
            self._endpoint = base
        elif base.endswith("/v1"):
            self._endpoint = f"{base}/chat/completions"
        else:
            self._endpoint = f"{base}/v1/chat/completions"
        self._timeout = timeout_seconds
        self._max_retries = max_retries

    @property
    def model(self) -> str:
        """执行模型标识（上报 executor_model_actual 用）。"""
        return self._model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """发起对话补全；返回 assistant message dict（含 usage 分离字段）。

        Raises:
            LLMError: 重试耗尽或不可重试错误。
        """
        body: dict[str, Any] = {"model": self._model, "messages": messages}
        if tools:
            body["tools"] = tools
        last_error = ""
        for attempt in range(self._max_retries + 1):
            if attempt:
                await asyncio.sleep(min(2 ** attempt, 8))
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        self._endpoint,
                        json=body,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                    )
            except httpx.HTTPError as exc:
                last_error = f"网络错误: {exc}"
                logger.warning(
                    "llm_request_retry",
                    extra={"attempt": attempt + 1, "error": last_error},
                )
                continue
            if response.status_code == 200:
                data = response.json()
                choice = (data.get("choices") or [{}])[0].get("message") or {}
                usage = data.get("usage") or {}
                return {
                    "message": choice,
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                }
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            if response.status_code in _NON_RETRY_STATUS:
                break
            logger.warning(
                "llm_request_retry",
                extra={"attempt": attempt + 1, "status": response.status_code},
            )
        raise LLMError(f"LLM 调用失败（{self._model}）：{last_error}")
