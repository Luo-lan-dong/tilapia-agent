"""Spec Builder Agent：对话判定 + 三文档串行直出 Markdown（ADR-046）。

四轮编排取代旧版「DeepSeek JSON Mode 单次调用」（ADR-010）：
轮 1 对话判定（追问或过渡语 + 末行 ``<decision>`` 标签），ready 后按
spec → tasks → checklist 串行直出 Markdown 文档——无 JSON、无转义，
单文档独立配额，根除偶发性解析失败；文档 content 增量真流式包装为
``specification_delta`` 事件，对话体验与旧版协议完全兼容（前端零改动）。

注：本文件略超 300 行上限（见 docs/standards/coding-standards.md 4.3）。
四轮编排、SSE 事件转换与草稿组装为同一对话状态机的强内聚流程，拆分会
引入额外的状态传递层，故按豁免场景保留并在此说明。
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import contextvars

from app.agents.spec_builder_parser import parse_decision
from app.agents.spec_builder_prompts import (
    CONVERSATION_SYSTEM_PROMPT_FAST,
    CONVERSATION_SYSTEM_PROMPT_THINKING,
    DECISION_READY,
    DOC_CHECKLIST_SYSTEM_PROMPT,
    DOC_SPEC_SYSTEM_PROMPT,
    DOC_TASKS_SYSTEM_PROMPT,
    DOC_VERIFY_SYSTEM_PROMPT,
)
from app.clients.deepseek import DeepSeekAPIError, DeepSeekClient
from app.core.errors import TaskSpecificationError
from app.services.llm_provider_service import LLMProviderService
from app.services.verify_plan import normalize_verify_plan

logger = logging.getLogger(__name__)

STREAM_CHUNK_SIZE = 2
STREAM_CHUNK_DELAY_SECONDS = 0.01
_FIELDS = ("spec", "tasks", "checklist")
#: 判据轮字段名（ADR-061 M4：输出 JSON 而非 Markdown，单独一轮处理）
_VERIFY_FIELD = "verify"
_DOC_PROMPTS = {
    "spec": DOC_SPEC_SYSTEM_PROMPT,
    "tasks": DOC_TASKS_SYSTEM_PROMPT,
    "checklist": DOC_CHECKLIST_SYSTEM_PROMPT,
}


def parse_verify_json(text: str) -> dict | None:
    """从 LLM 输出中容错提取 verify.json（去围栏 → 取首尾花括号 → 归一化）。

    解析失败返回 ``None``（= 无判据 → 节点按「无法验证」上报），
    绝不因判据解析问题丢掉已生成的三件套。
    """
    raw = text.strip()
    if raw.startswith("```"):
        # 去掉 ```json ... ``` 围栏
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3]
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        logger.warning(
            "verify_plan_json_not_found",
            extra={"event": "verify_plan_json_not_found", "length": len(text)},
        )
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except (ValueError, TypeError):
        logger.warning(
            "verify_plan_json_parse_failed",
            extra={"event": "verify_plan_json_parse_failed", "length": len(text)},
        )
        return None
    return normalize_verify_plan(parsed)


def _bind_request_id() -> None:
    """将当前上下文的 request_id 绑定到日志上下文。"""
    request_id = contextvars.get_contextvars().get("request_id")
    if request_id:
        contextvars.bind_contextvars(request_id=request_id)


class SpecBuilderAgent:
    """基于 DeepSeek 的 Spec Builder Agent（ADR-046 四轮编排）。"""

    def __init__(
        self,
        session: AsyncSession,
        deepseek_client: DeepSeekClient | None = None,
    ) -> None:
        """初始化 Agent。

        Args:
            session: 异步数据库会话，用于读取当前 LLM 配置。
            deepseek_client: DeepSeek 客户端实例，未提供时从数据库配置创建。
        """
        self._session = session
        self._client = deepseek_client

    async def _get_client(self) -> DeepSeekClient:
        """获取或创建 DeepSeek 客户端。"""
        if self._client is None:
            config = await LLMProviderService().get_active_provider(self._session)
            self._client = DeepSeekClient(config, scene="spec_chat")
        return self._client

    async def chat(
        self,
        messages: list[dict[str, str]],
        current_specification: dict[str, Any] | None,
        mode: str = "thinking",
    ) -> dict[str, Any]:
        """非流式入口：消费流式编排并收集最终结果（结构兼容旧版）。

        Raises:
            TaskSpecificationError: 流式编排以 error 事件终止时抛出
               （语义与旧版解析失败一致，由端点层统一异常处理）。
        """
        _bind_request_id()
        reply_parts: list[str] = []
        specification: dict[str, str] | None = None
        is_ready = False
        model = ""
        async for event in self.chat_stream(messages, current_specification, mode=mode):
            name = event["event"]
            data = event.get("data") or {}
            if name == "meta":
                model = str(data.get("model") or "")
            elif name == "chunk":
                reply_parts.append(data.get("delta", ""))
            elif name == "specification":
                specification = data
            elif name == "status" and "is_ready_to_create" in data:
                is_ready = bool(data["is_ready_to_create"])
            elif name == "error":
                raise TaskSpecificationError(str(data.get("message") or "三件套生成失败"))

        return {
            "ai_reply": "".join(reply_parts),
            "specification": specification
            or {"spec": "", "tasks": "", "checklist": "", "verify": None},
            "is_ready_to_create": is_ready,
            "model": model,
            "mode": mode,
        }

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        current_specification: dict[str, Any] | None = None,
        mode: str = "thinking",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式处理一轮对话：判定轮 + 三文档轮（ADR-046）。

        事件序列：``meta`` → （``thinking``）→ ``chunk``（判定回复）→
        need_info 时 ``status{is_ready_to_create:false}`` + ``done``；
        ready 时 ``status{message}`` → 三轮 ``specification_delta`` 真流式
        → ``specification`` → ``status{is_ready_to_create:true}`` → ``done``。
        任一轮 LLM 失败产出 ``error`` + ``done`` 终止。
        """
        _bind_request_id()
        client = await self._get_client()
        model = client._config.model  # noqa: SLF001 — 与旧版同模式读取
        reasoning = mode == "thinking"

        logger.info(
            "spec_builder_stream_started",
            extra={
                "event": "spec_builder_stream_started",
                "message_count": len(messages),
                "has_current_specification": current_specification is not None,
                "mode": mode,
                "model": model,
            },
        )
        yield {"event": "meta", "data": {"model": model, "mode": mode}}

        # 轮 1：对话判定（reasoning 实时透传，正文收完解析 decision）
        conversation_messages = self._build_conversation_messages(
            messages, current_specification, mode
        )
        round_content: str | None = None
        async for event in self._receive_conversation_round(
            client, conversation_messages, reasoning
        ):
            if event["event"] == "_content":
                round_content = event["data"]
            else:
                yield event
        if round_content is None:  # 判定轮已产出 error + done
            return

        decision, reply_text = parse_decision(round_content)
        logger.info(
            "spec_builder_decision",
            extra={"event": "spec_builder_decision", "decision": decision},
        )
        for index in range(0, len(reply_text), STREAM_CHUNK_SIZE):
            yield {
                "event": "chunk",
                "data": {"delta": reply_text[index : index + STREAM_CHUNK_SIZE]},
            }
            await asyncio.sleep(STREAM_CHUNK_DELAY_SECONDS)

        if decision != DECISION_READY:
            yield {"event": "status", "data": {"is_ready_to_create": False}}
            yield {"event": "done", "data": {}}
            logger.info(
                "spec_builder_need_info",
                extra={"event": "spec_builder_need_info"},
            )
            return

        # 文档轮：spec → tasks → checklist 串行真流式
        yield {"event": "status", "data": {"message": "正在编写规范文档…"}}
        specification: dict[str, Any] = dict.fromkeys(_FIELDS, "")
        specification[_VERIFY_FIELD] = None
        if current_specification:
            for field in _FIELDS:
                specification[field] = str(current_specification.get(field) or "")
            specification[_VERIFY_FIELD] = normalize_verify_plan(
                current_specification.get(_VERIFY_FIELD)
            )

        for field in _FIELDS:
            doc_messages = self._build_doc_messages(
                messages, current_specification, specification, field
            )
            logger.info(
                "spec_builder_doc_round_started",
                extra={"event": "spec_builder_doc_round_started", "field": field},
            )
            failed = False
            async for event in self._stream_doc_round(client, doc_messages, reasoning):
                name = event["event"]
                if name == "_delta":
                    delta = event["data"]
                    specification[field] += delta
                    yield {
                        "event": "specification_delta",
                        "data": {"field": field, "delta": delta},
                    }
                elif name == "error":
                    failed = True
                    yield event
                    break
                else:
                    yield event
            if failed:
                yield {"event": "done", "data": {}}
                return

        # 判据轮（ADR-061 M4，Q6：中枢 LLM 生成 verify.json）：
        # 失败**不**终止三件套——判据置 None，节点会按「无法验证」上报（不许假成功）。
        yield {"event": "status", "data": {"message": "正在编写验收判据…"}}
        verify_text = ""
        verify_failed = False
        verify_messages = self._build_verify_messages(messages, specification)
        async for event in self._stream_doc_round(client, verify_messages, reasoning):
            name = event["event"]
            if name == "_delta":
                verify_text += event["data"]
                yield {
                    "event": "specification_delta",
                    "data": {"field": _VERIFY_FIELD, "delta": event["data"]},
                }
            elif name == "error":
                verify_failed = True
                break
            else:
                yield event
        if verify_failed:
            logger.warning(
                "spec_builder_verify_round_failed",
                extra={"event": "spec_builder_verify_round_failed"},
            )
            yield {
                "event": "status",
                "data": {"message": "验收判据生成失败，将按「无法验证」处理（可在对话中补充）"},
            }
        plan = parse_verify_json(verify_text) if verify_text.strip() else None
        specification[_VERIFY_FIELD] = plan
        logger.info(
            "spec_builder_verify_plan_ready",
            extra={
                "event": "spec_builder_verify_plan_ready",
                "checks": len((plan or {}).get("checks") or []),
                "must_pass": len(
                    [item for item in (plan or {}).get("checks") or [] if item.get("must_pass")]
                ),
            },
        )

        yield {"event": "specification", "data": dict(specification)}
        yield {"event": "status", "data": {"is_ready_to_create": True}}
        yield {"event": "done", "data": {}}
        logger.info(
            "spec_builder_stream_completed",
            extra={
                "event": "spec_builder_stream_completed",
                "mode": mode,
                "model": model,
                "field_lengths": {k: len(v) for k, v in specification.items()},
            },
        )

    def _build_conversation_messages(
        self,
        messages: list[dict[str, str]],
        current_specification: dict[str, Any] | None,
        mode: str,
    ) -> list[dict[str, str]]:
        """构建对话判定轮消息：判定提示词 + 对话历史 + 当前草案。"""
        system_prompt = (
            CONVERSATION_SYSTEM_PROMPT_THINKING
            if mode == "thinking"
            else CONVERSATION_SYSTEM_PROMPT_FAST
        )
        prompt_messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            *messages,
        ]
        if current_specification:
            prompt_messages.append(
                {
                    "role": "system",
                    "content": (
                        "当前三件套草案：\n"
                        f"{json.dumps(current_specification, ensure_ascii=False)}"
                    ),
                }
            )
        return prompt_messages

    def _build_doc_messages(
        self,
        messages: list[dict[str, str]],
        current_specification: dict[str, Any] | None,
        specification: dict[str, Any],
        field: str,
    ) -> list[dict[str, str]]:
        """构建文档轮消息：写作提示词 + 草案 + 前序已生成文档。"""
        parts: list[str] = [_DOC_PROMPTS[field]]
        if current_specification:
            parts.append(
                "当前三件套草案（作为修订基础）：\n"
                f"{json.dumps(current_specification, ensure_ascii=False)}"
            )
        index = _FIELDS.index(field)
        for done_field in _FIELDS[:index]:
            content = specification.get(done_field) or ""
            if content:
                parts.append(f"已生成的 {done_field} 文档：\n{content}")
        return [{"role": "system", "content": "\n\n".join(parts)}, *messages]

    def _build_verify_messages(
        self,
        messages: list[dict[str, str]],
        specification: dict[str, Any],
    ) -> list[dict[str, str]]:
        """构建判据轮消息：写作提示词 + 三件套全文（判据必须落在具体文件/命令上）。"""
        parts: list[str] = [DOC_VERIFY_SYSTEM_PROMPT]
        for field in _FIELDS:
            content = specification.get(field) or ""
            if content:
                parts.append(f"已生成的 {field} 文档：\n{content}")
        return [{"role": "system", "content": "\n\n".join(parts)}, *messages]

    async def _receive_conversation_round(
        self,
        client: DeepSeekClient,
        prompt_messages: list[dict[str, str]],
        reasoning: bool,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """判定轮流式接收：reasoning 实时透传，正文收集后经 ``_content`` 产出。

        失败时产出 ``error`` + ``done`` 后终止（``_content`` 不再出现）。
        """
        parts: list[str] = []
        try:
            async for chunk in client.chat_completion_stream(
                prompt_messages, reasoning=reasoning
            ):
                if chunk.get("reasoning"):
                    yield {"event": "thinking", "data": {"delta": chunk["reasoning"]}}
                if chunk.get("content"):
                    parts.append(chunk["content"])
        except DeepSeekAPIError as exc:
            logger.warning(
                "spec_builder_conversation_round_failed",
                extra={
                    "event": "spec_builder_conversation_round_failed",
                    "reason": exc.message,
                    "status_code": exc.status_code,
                },
                exc_info=True,
            )
            yield {"event": "error", "data": {"message": exc.message}}
            yield {"event": "done", "data": {}}
            return
        yield {"event": "_content", "data": "".join(parts)}

    async def _stream_doc_round(
        self,
        client: DeepSeekClient,
        doc_messages: list[dict[str, str]],
        reasoning: bool,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """文档轮流式接收：content 增量经 ``_delta`` 产出（真流式）。

        失败时产出 ``error`` 后终止（调用方补 ``done`` 收尾）。
        """
        try:
            async for chunk in client.chat_completion_stream(doc_messages, reasoning=reasoning):
                if chunk.get("reasoning"):
                    yield {"event": "thinking", "data": {"delta": chunk["reasoning"]}}
                if chunk.get("content"):
                    yield {"event": "_delta", "data": chunk["content"]}
        except DeepSeekAPIError as exc:
            logger.warning(
                "spec_builder_doc_round_failed",
                extra={
                    "event": "spec_builder_doc_round_failed",
                    "reason": exc.message,
                    "status_code": exc.status_code,
                },
                exc_info=True,
            )
            yield {"event": "error", "data": {"message": exc.message}}
