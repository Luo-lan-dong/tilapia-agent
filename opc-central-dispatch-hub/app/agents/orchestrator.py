"""中枢对话 Orchestrator Agent：function calling 技能循环（ADR-033）。

统一对话链路：LLM 根据用户意图自主决定调用哪些技能（tools），技能执行
过程以 ``step_started`` / ``step_finished`` SSE 事件对外呈现（前端步骤
卡片），技能产生的中间事件（``chunk`` / ``specification_delta`` 等）
原样转发；技能结果回传 LLM 继续推理，直到产出最终回答。

工具循环使用非流式调用（简化 tool_calls 解析，规避流式增量拼装风险）；
最终回答按打字机分片下发 ``chunk`` 事件，与既有前端渲染协议兼容。

注：本文件超过 300 行上限（见 docs/standards/coding-standards.md 4.3）。
技能循环、事件协议转换与 /spec 显式命令（ADR-036）共享同一套 SSE 事件
协议与上下文构造，拆分会割裂协议一致性，故按豁免场景保留并在此说明。
"""

import asyncio
import base64
import contextlib
import json
import logging
import math
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import contextvars

from app.agents.orchestrator_prompts import (
    ORCHESTRATOR_DOOM_LOOP_PROMPT,
    ORCHESTRATOR_FAKE_TOOL_CORRECTION,
    ORCHESTRATOR_FORCE_FINAL_PROMPT,
    ORCHESTRATOR_NO_PROGRESS_PROMPT,
    ORCHESTRATOR_SPEC_CONTEXT_TEMPLATE,
    build_system_prompt,
)
from app.clients.deepseek import (
    RETRYABLE_STATUS_CODES,
    DeepSeekAPIError,
    DeepSeekClient,
)
from app.core.config import get_settings
from app.models.session_attachment import SessionAttachment
from app.models.task_building_session import TaskBuildingSession
from app.services import steering_service, tool_call_log_service
from app.services.context_compaction_service import (
    ContextCompactionService,
    calibrate_chars_per_token,
    estimate_chars_per_token,
    estimate_messages_tokens,
    is_context_overflow_error,
    measure_payload_chars,
    project_messages,
    should_compact_now,
)
from app.services.context_environment_service import (
    build_environment_block,
    build_environment_update_message,
    query_cluster_summary,
)
from app.services.experience_service import ExperienceService
from app.services.llm_provider_service import LLMProviderConfig, LLMProviderService
from app.skills.base import RESULT_EVENT, SkillContext, SkillResult
from app.skills.registry import (
    SKILL_REGISTRY,
    find_skill,
    get_enabled_skills,
    to_openai_tools,
)

logger = logging.getLogger(__name__)

REPLY_CHUNK_SIZE = 4
ARGS_SUMMARY_MAX = 200
RESULT_SUMMARY_MAX = 400
_TOOL_MESSAGE_EVENT = "_tool_message"
# 内部事件（ADR-040）：本轮工具轨迹交端点按序落库，不透传前端
_TRAJECTORY_EVENT = "_history_trajectory"
# 内部事件（ADR-045）：边界提升的插话消息交端点插入持久化序列
_STEERING_EVENT = "_steering_messages"
# /spec 显式命令（ADR-036）：消息 strip 后精确匹配即强制生成三件套
_SPEC_COMMAND = "/spec"


def _match_spec_command(messages: list[dict[str, str]]) -> bool:
    """检测最后一轮用户消息是否为 /spec 显式命令（strip 后精确匹配）。"""
    for message in reversed(messages):
        if message.get("role") == "user":
            return message.get("content", "").strip() == _SPEC_COMMAND
    return False


def _tool_calls_signature(tool_calls: list[dict[str, Any]]) -> str:
    """工具轮调用签名（ADR-043 doom loop 检测）：技能名 + 规范化参数。

    参数经 JSON 解析后 ``sort_keys`` 序列化，键序无关；解析失败回退原样
    字符串。一轮多个 tool_calls 整体算一个签名。
    """
    items: list[dict[str, str]] = []
    for call in tool_calls:
        function = call.get("function", {}) if isinstance(call, dict) else {}
        raw = function.get("arguments")
        try:
            args_json = json.dumps(
                json.loads(raw) if isinstance(raw, str) else raw,
                sort_keys=True,
                ensure_ascii=False,
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            args_json = str(raw)
        items.append({"name": str(function.get("name", "")), "arguments": args_json})
    return json.dumps(items, sort_keys=True, ensure_ascii=False)


async def _apply_steered_messages(
    steered: list[str],
    prompt_messages: list[dict[str, Any]],
) -> AsyncGenerator[dict[str, Any], None]:
    """提升排队插话进 prompt（ADR-045）。

    user 消息 append 进 prompt（本轮后续 LLM 调用可见），逐条下发
    ``steering_applied`` 前端事件（清除排队标记），最后发内部事件
    ``_steering_messages``（端点插入持久化序列，不透传前端）。
    """
    steered_entries: list[dict[str, Any]] = []
    for content in steered:
        entry: dict[str, Any] = {"role": "user", "content": content}
        prompt_messages.append(entry)
        steered_entries.append(dict(entry))
        yield {"event": "steering_applied", "data": {"content": content}}
    if steered_entries:
        yield {"event": _STEERING_EVENT, "data": {"messages": steered_entries}}


async def _experience_pending_hint(session, session_id: str | None) -> str:
    """会话 pending 经验条目的收尾提示（ADR-050 通道 a）。

    无条目/查询异常返回空串（经验是增益不是依赖，兜底不阻断收尾）。
    """
    if not session_id:
        return ""
    try:
        records = await ExperienceService().list_session_pending(session, session_id)
    except Exception:  # noqa: BLE001 — 提示属增益，失败静默
        logger.warning(
            "experience_pending_hint_failed",
            extra={"event": "experience_pending_hint_failed"},
            exc_info=True,
        )
        return ""
    if not records:
        return ""
    lines = "\n".join(
        f"- [{record.tool or '通用'}] {record.error_signature[:80]}" for record in records[:5]
    )
    more = f"（等 {len(records)} 条）" if len(records) > 5 else ""
    return (
        "\n\n---\n💾 本次对话记录了 "
        f"{len(records)} 条待总结的踩坑经验{more}：\n{lines}\n"
        "如需沉淀为长期记忆，回复「总结这些经验」，我会提炼根因与解法草案，"
        "经您确认后存入经验库（也可在左侧「记忆」页面查看管理）。"
    )


async def _log_steering(session_id: str | None, steered: list[str]) -> None:
    """Steering 提升链路采集（ADR-047）：有插话提升时记录条数与内容。"""
    if session_id and steered:
        await tool_call_log_service.record(
            kind="steering",
            name="drain",
            session_id=session_id,
            result_summary={"count": len(steered), "contents": steered},
        )


# 伪工具调用标记（DSML）：模型在 tools 缺失时可能以文本形式输出，
# 检测到即触发一次性修正重试（见 chat_stream），修正失败则剥离标记输出
_FAKE_TOOL_MARKERS = ("<|DSML|>", "</|DSML|>", "<|DSML", "<invoke", "</invoke")


def _contains_fake_tool_syntax(text: str) -> bool:
    """检测回答文本中是否包含伪工具调用标记。"""
    return any(marker in text for marker in _FAKE_TOOL_MARKERS)


def _strip_fake_tool_syntax(text: str) -> str:
    """剥离回答文本中的伪工具调用标记（兜底清理）。"""
    cleaned = text
    for marker in _FAKE_TOOL_MARKERS:
        cleaned = cleaned.replace(marker, "")
    return cleaned.strip()


def _bind_request_id() -> None:
    """将当前上下文的 request_id 绑定到日志上下文。"""
    request_id = contextvars.get_contextvars().get("request_id")
    if request_id:
        contextvars.bind_contextvars(request_id=request_id)


class OrchestratorAgent:
    """中枢对话 Agent：技能调用循环 + 统一 SSE 事件流。"""

    def __init__(
        self,
        session: AsyncSession,
        deepseek_client: DeepSeekClient | None = None,
    ) -> None:
        """初始化 Agent。

        Args:
            session: 异步数据库会话（技能执行与 LLM 配置读取共用）。
            deepseek_client: 可注入的 DeepSeek 客户端（测试用）。
        """
        self._session = session
        self._client = deepseek_client

    async def _get_client(self) -> DeepSeekClient:
        """获取或创建 DeepSeek 客户端（复用 ADR-030 使用中模型）。"""
        if self._client is None:
            config = await LLMProviderService().get_active_provider(self._session)
            self._client = DeepSeekClient(config, scene="orchestrator")
        return self._client

    async def _load_attachment_image_parts(
        self, attachment_ids: list[Any], notices: list[str]
    ) -> list[dict[str, Any]]:
        """按附件 ID 读取图片文件并转为 OpenAI image_url parts（ADR-039）。

        附件缺失/类型不符时不中断对话，仅记录 notice 由调用方下发提示。
        """
        parts: list[dict[str, Any]] = []
        for raw_id in attachment_ids:
            try:
                attachment_id = uuid.UUID(str(raw_id))
            except ValueError:
                notices.append(f"图片附件 ID 无效：{raw_id}")
                continue
            record = await self._session.get(SessionAttachment, attachment_id)
            if record is None or record.kind != "image":
                notices.append(f"图片附件不存在或类型不支持：{raw_id}")
                continue
            path = Path(get_settings().attachment_storage_dir) / record.storage_path
            if not path.is_file():
                notices.append(f"图片附件文件缺失：{record.file_name}")
                continue
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{record.mime};base64,{encoded}"},
                }
            )
        if parts:
            logger.info(
                "orchestrator_attachment_images_injected",
                extra={
                    "event": "orchestrator_attachment_images_injected",
                    "count": len(parts),
                },
            )
        return parts

    async def _load_attachment_records(
        self, attachment_ids: list[Any]
    ) -> dict[uuid.UUID, SessionAttachment]:
        """按附件 ID 批量读取记录（无效/缺失 ID 跳过）。"""
        records: dict[uuid.UUID, SessionAttachment] = {}
        for raw_id in attachment_ids:
            try:
                attachment_id = uuid.UUID(str(raw_id))
            except ValueError:
                continue
            record = await self._session.get(SessionAttachment, attachment_id)
            if record is not None:
                records[attachment_id] = record
        return records

    def _describe_file_attachment(self, record: SessionAttachment) -> str:
        """非图片附件的结构化描述（AI 部署/处理文件的可见信息）。"""
        kind_label = "zip 压缩包" if record.kind == "archive" else "文件"
        size_kb = max(record.size // 1024, 1)
        line = f"- {record.file_name}（{kind_label}，约 {size_kb} KB，附件 ID：{record.id}）"
        base_url = (get_settings().hub_public_url or "").rstrip("/")
        if base_url:
            line += (
                f"\n  下载地址：{base_url}/api/v1/sessions/"
                f"{record.session_id}/attachments/{record.id}/content"
                "（可在目标服务器用 Bash: curl -L '<下载地址>' -o '<文件名>' 拉取）"
            )
        else:
            line += (
                "\n  注意：中枢外部地址（hub_public_url）未配置，暂无法生成服务器"
                "可直接 curl 的下载链接。请引导用户到「系统设置 → 网络」把外部地址"
                "配置为目标服务器可访问的本中枢地址后重试；或改用 Upload 工具链路。"
            )
        return line

    async def _inject_attachment_images(
        self,
        messages: list[dict[str, Any]],
        supports_vision: bool,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """把带附件的用户消息转换为模型可读 content（ADR-039）。

        按 kind 分类注入（修复：此前全部附件按「图片附件」占位，zip 被
        图片分支过滤后 AI 可见信息为零，导致压缩包被识别为图片）：
        - ``image``：最后一条 user 消息注入视觉 parts（历史轮次转文本占位，
          避免每轮重发全量历史时图片重复计费）；模型不支持视觉时不注入并
          下发 warning；
        - ``archive``/``file``：注入结构化文本（文件名/类别/大小/附件 ID/
          下载地址），AI 可据此用 Bash/Upload 把产物包送上目标服务器。

        Returns:
            (转换后的消息列表, 提示信息列表)。
        """
        user_indexes = [i for i, m in enumerate(messages) if m.get("role") == "user"]
        last_user_index = user_indexes[-1] if user_indexes else -1
        notices: list[str] = []
        converted: list[dict[str, Any]] = []
        attachment_message_count = sum(
            1 for m in messages if m.get("role") == "user" and (m.get("attachments") or [])
        )
        if attachment_message_count:
            logger.info(
                "orchestrator_attachment_messages_detected",
                extra={
                    "event": "orchestrator_attachment_messages_detected",
                    "message_count": attachment_message_count,
                    "supports_vision": supports_vision,
                },
            )
        for index, message in enumerate(messages):
            content = message.get("content", "")
            # 非 user 消息原样透传（ADR-040）：assistant 的 tool_calls 与
            # tool 消息的 tool_call_id 必须完整保留，削平会导致 provider 400
            # （tool 消息失去前置 assistant(tool_calls) 关联）
            if message.get("role") != "user":
                converted.append(dict(message))
                continue
            attachment_refs = message.get("attachments") or []
            attachment_ids = [
                ref.get("id") for ref in attachment_refs if isinstance(ref, dict) and ref.get("id")
            ]
            if not attachment_ids:
                converted.append({"role": "user", "content": content})
                continue
            records = await self._load_attachment_records(attachment_ids)
            image_ids = [str(aid) for aid, record in records.items() if record.kind == "image"]
            file_lines = [
                self._describe_file_attachment(record)
                for record in records.values()
                if record.kind != "image"
            ]
            for raw_id in attachment_ids:
                if not any(str(aid) == str(raw_id) for aid in records):
                    notices.append(f"附件不存在：{raw_id}")
            placeholder_parts: list[str] = []
            if image_ids:
                image_names = [records[uuid.UUID(aid)].file_name for aid in image_ids]
                placeholder_parts.append(f"[图片附件：{', '.join(image_names)}]")
            if file_lines:
                placeholder_parts.append("[文件附件]\n" + "\n".join(file_lines))
            placeholder = "\n".join(placeholder_parts)
            if index == last_user_index and supports_vision:
                parts: list[dict[str, Any]] = []
                if content:
                    parts.append({"type": "text", "text": content})
                if file_lines:
                    # 文件附件信息以文本 part 注入（与图片 parts 共存）
                    parts.append({"type": "text", "text": "[文件附件]\n" + "\n".join(file_lines)})
                parts.extend(await self._load_attachment_image_parts(image_ids, notices))
                converted.append({"role": "user", "content": parts})
                continue
            if index == last_user_index and not supports_vision and image_ids:
                notices.append(
                    "当前对话模型不支持图片识别，图片未发送给模型；"
                    "可在系统设置中切换勾选了「支持图片」的模型"
                )
            converted.append(
                {"role": "user", "content": f"{content}\n{placeholder}" if placeholder else content}
            )
        return converted, notices

    def _build_messages(
        self,
        messages: list[dict[str, str]],
        current_specification: dict[str, Any] | None,
        skills: list[Any],
        disabled_skills: list[Any],
        context_compaction: dict[str, Any] | None = None,
        environment_block: str | None = None,
        with_tools: bool = True,
        todos_block: str | None = None,
    ) -> list[dict[str, Any]]:
        """构建动态系统提示词与对话历史消息列表（ADR-040/041/042/056）。

        历史消息透传工具轨迹：assistant 的 ``tool_calls`` 与 ``role:"tool"``
        结果原样回传 LLM（多轮思考的地基）；``steps`` 仍为前端展示层数据
        （ADR-033）不进请求体；``thinking`` 在**带 tools 的请求**中作为
        ``reasoning_content`` 回传（ADR-056 决策 D4 路线 B：官方要求带 tools 时
        必须回传推理内容），不带 tools 时剥离（官方明确该字段被忽略）。
        兼容旧格式（无 tool_calls 的历史行为不变）；assistant 带 tool_calls 但
        缺对应 tool 响应的脏数据自动补空 tool 消息，避免下一轮调用 400。

        存在压缩点（ADR-041）时做投影：头部注入摘要 checkpoint（user 角色），
        仅携带压缩点之后的消息——原始历史保留在会话 messages 列，模型可经
        history_recall 技能回查。

        ``environment_block``（ADR-042）拼入 system 尾部；会话内应传入快照
        版本（Baseline 不可变），环境变化经增量消息（尾部追加）传达。

        Args:
            with_tools: 本次请求是否携带 tools（决定 ``reasoning_content`` 是否回流）。
        """
        history_messages: list[dict[str, Any]] = []
        seen_call_ids: set[str] = set()
        for m in messages:
            role = m.get("role")
            if role == "assistant":
                entry: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
                if m.get("tool_calls"):
                    entry["tool_calls"] = m["tool_calls"]
                    for call in m["tool_calls"]:
                        call_id = str(call.get("id") or "")
                        if call_id:
                            seen_call_ids.add(call_id)
                    # ADR-056 决策 D4（路线 B）：官方要求「请求带 tools 时
                    # reasoning_content 必须回传」。历史 assistant 的推理内容
                    # 由 `thinking` 字段承载（前端展示与协议回传同一份文本），
                    # 仅在带 tools 的请求中回流；不带 tools 的收尾轮不回传
                    # （官方明确不带 tools 时该字段被忽略）。
                    thinking = str(m.get("thinking") or "")
                    if with_tools and thinking:
                        entry["reasoning_content"] = thinking
                history_messages.append(entry)
            elif role == "tool":
                # 孤儿 tool 过滤（修复 400：前端回传渲染态与轨迹持久化不一致时，
                # tool 消息的 assistant(tool_calls) 载体可能丢失——协议要求 tool
                # 必须紧跟带 tool_calls 的 assistant，否则 provider 400）
                if str(m.get("tool_call_id") or "") not in seen_call_ids:
                    logger.warning(
                        "orphan_tool_message_dropped",
                        extra={
                            "event": "orphan_tool_message_dropped",
                            "tool_call_id": str(m.get("tool_call_id"))[:64],
                        },
                    )
                    continue
                history_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.get("tool_call_id", ""),
                        "content": m.get("content", ""),
                    }
                )
            else:
                history_messages.append({"role": role, "content": m.get("content", "")})
        # 脏数据兜底：tool_calls 缺 tool 响应 → 补空消息（协议要求一问一答）
        answered_ids = {
            msg["tool_call_id"] for msg in history_messages if msg.get("role") == "tool"
        }
        patched_messages: list[dict[str, Any]] = []
        for msg in history_messages:
            patched_messages.append(msg)
            for tool_call in msg.get("tool_calls") or []:
                call_id = tool_call.get("id")
                if call_id and call_id not in answered_ids:
                    patched_messages.append(
                        {"role": "tool", "tool_call_id": call_id, "content": ""}
                    )
                    answered_ids.add(call_id)

        system_parts = [
            build_system_prompt(
                enabled=[(skill.name, skill.description) for skill in skills],
                disabled=[(skill.name, skill.description) for skill in disabled_skills],
            )
        ]
        if environment_block:
            system_parts.append(environment_block)
        if todos_block:
            # ADR-067 修订（2026-10-07 用户实测）：中枢待办清单实时注入 system——
            # 编排 AI 此前只靠对话历史记忆维护待办，跨轮/唤醒轮会凭印象重列，
            # 造成新旧清单叠加；注入实时状态后每次 todo_write 前都有据可依。
            system_parts.append(todos_block)
        prompt_messages: list[dict[str, Any]] = [
            {"role": "system", "content": "\n\n".join(system_parts)},
            *project_messages(patched_messages, context_compaction),
        ]
        if current_specification:
            prompt_messages.append(
                {
                    "role": "system",
                    "content": ORCHESTRATOR_SPEC_CONTEXT_TEMPLATE.format(
                        spec_json=json.dumps(current_specification, ensure_ascii=False)
                    ),
                }
            )
        return prompt_messages

    async def _load_todos_block(self, session_id: str | None) -> str | None:
        """加载当前中枢待办清单文本块（注入 system，供编排 AI 对照维护）。

        Owner 解析与 ``todo_write`` 技能一致：会话绑任务 → 任务维度；未绑 →
        会话维度（ADR-067 待办先行）。查询失败/清单为空返回 None（旁路，
        不阻塞对话）。
        """
        if not session_id:
            return None
        try:
            from app.services import orchestrator_todo_service as todo_service

            chat_session = await self._session.get(
                TaskBuildingSession, uuid.UUID(str(session_id))
            )
            if chat_session is None:
                return None
            if chat_session.task_id is not None:
                rows = await todo_service.list_todos(
                    self._session, task_id=chat_session.task_id
                )
                owner = f"任务 {chat_session.task_id}"
            else:
                rows = await todo_service.list_todos(
                    self._session, session_id=chat_session.id
                )
                owner = f"会话 {chat_session.id}"
            if not rows:
                return None
            completed = sum(1 for row in rows if row.status == "completed")
            return (
                f"【中枢待办当前状态（owner={owner}，已完成 {completed}/{len(rows)}）】\n"
                "以下是本会话中枢待办清单的**实时状态**。维护待办（todo_write）前必须"
                "先对照它：不要凭记忆重列已完成/进行中的事项；需求确认后计划细化或"
                "整体变更时用 action=reset 整份重建；单个事项推进用 start/complete。\n"
                + todo_service.render_lines(rows)
            )
        except Exception:  # noqa: BLE001 — 上下文注入是增强，失败不阻塞对话
            logger.warning(
                "orchestrator_todos_context_load_failed",
                extra={
                    "event": "orchestrator_todos_context_load_failed",
                    "session_id": session_id,
                },
                exc_info=True,
            )
            return None

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        current_specification: dict[str, Any] | None = None,
        mode: str | None = None,
        session_id: str | None = None,
        parent_task_id: str | None = None,
        output_dir: str | None = None,
        max_rounds_override: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """处理一轮对话，产出统一 SSE 事件流。

        事件序列：``meta`` → （``step_started`` / 技能中间事件 /
        ``step_finished``）× N → ``chunk`` × N → ``done``；LLM 配置缺失
        或调用失败时产出 ``error`` + ``done``。
        """
        _bind_request_id()
        # 思考模式归一（ADR-056 修订）：前端显式选择（thinking/fast）优先，
        # 未传时回退全局配置。归一后的 mode 同时驱动：编排主循环 reasoning
        # 开关、SkillContext（三件套生成深度）与 meta 回显——此前主循环
        # 只认全局配置，前端切换思考/快速模式对编排轮不生效（2026-09-29 实测）。
        if mode is None:
            mode = "thinking" if get_settings().orchestrator_thinking_enabled else "fast"
        thinking_enabled = mode == "thinking"
        # /spec 显式命令（ADR-036）：确定性强制执行 generate_spec_docs——
        # 不经 LLM 决策轮（技能禁用也生效），也不依赖 Orchestrator 自身
        # 客户端（技能内自建），故置于 _get_client 之前
        if _match_spec_command(messages):
            async for event in _run_forced_spec(
                session=self._session,
                messages=messages,
                current_specification=current_specification,
                mode=mode,
            ):
                yield event
            return
        try:
            client = await self._get_client()
        except ValueError as exc:
            logger.warning(
                "orchestrator_llm_config_missing",
                extra={"event": "orchestrator_llm_config_missing", "reason": str(exc)},
            )
            yield {"event": "error", "data": {"message": f"LLM 配置缺失，技能对话不可用：{exc}"}}
            yield {"event": "done", "data": {}}
            return

        # 会话附件图片注入（ADR-039）：使用中模型支持视觉时把最后一轮
        # 用户消息转为多模态 content；不支持时以 warning 事件提示
        supports_vision = bool(client._config.supports_vision)  # noqa: SLF001
        messages, attachment_notices = await self._inject_attachment_images(
            messages, supports_vision
        )

        model = client._config.model  # noqa: SLF001 — 与 SpecBuilderAgent 同模式读取
        # Steering（ADR-045）：先于 meta 注册活跃流，保证前端收到 meta 解禁
        # 输入后立即投递插话不会 409
        if session_id:
            steering_service.open_stream(session_id)
        yield {"event": "meta", "data": {"model": model, "mode": mode}}
        for notice in attachment_notices:
            yield {"event": "warning", "data": {"message": notice}}

        skills = await get_enabled_skills(self._session)
        enabled_keys = {skill.name for skill in skills}
        disabled_skills = [cls() for cls in SKILL_REGISTRY if cls.name not in enabled_keys]
        tools = to_openai_tools(skills)
        # 会话行（ADR-041）：压缩投影、压缩落库与历史回查的数据基础
        building_session: TaskBuildingSession | None = None
        if session_id:
            try:
                building_session = await self._session.get(
                    TaskBuildingSession, uuid.UUID(session_id)
                )
            except ValueError:
                building_session = None
        compaction = building_session.context_compaction if building_session else None
        # 环境上下文（ADR-042）：Baseline（快照版本，会话内稳定）+ 变化增量。
        # system 中的环境块使用快照版本而非实时，保证 provider cache 前缀稳定。
        current_env = build_environment_block(
            await query_cluster_summary(self._session),
            model,
        )
        environment_snapshot = building_session.context_environment if building_session else None
        if environment_snapshot and environment_snapshot.get("block"):
            env_for_system = str(environment_snapshot["block"])
        else:
            env_for_system = current_env
        # 中枢待办实时状态（ADR-067 修订）：本轮开始时查询一次，作为 system
        # 上下文注入——编排 AI 维护待办前有据可依，不再凭记忆重列
        todos_block = await self._load_todos_block(session_id)
        prompt_messages = self._build_messages(
            messages,
            current_specification,
            skills,
            disabled_skills,
            compaction,
            environment_block=env_for_system,
            todos_block=todos_block,
        )
        max_rounds = (
            max_rounds_override
            if max_rounds_override is not None
            else get_settings().orchestrator_max_tool_rounds
        )
        doom_threshold = get_settings().orchestrator_doom_loop_threshold
        # 进度检测（ADR-058 D2）：连续多少轮没引入新技能即判定原地打转
        no_progress_threshold = get_settings().orchestrator_no_progress_rounds
        # 逼近上限的警示阈值（ADR-058 D3）：按比例取整，至少 1
        warning_at = max(
            1, math.ceil(max_rounds * get_settings().orchestrator_rounds_warning_ratio)
        )
        # 思考模式开关已在函数入口按前端 mode 归一（thinking_enabled）
        # Doom Loop 检测（ADR-043）：最近各工具轮的调用签名（连续相同判定用）
        recent_signatures: list[str] = []
        # 进度检测状态（ADR-058）：已返回过结果的技能名 + 连续无进展轮数 + 是否已警示过
        seen_skill_names: set[str] = set()
        no_progress_streak = 0
        rounds_warning_emitted = False
        seq = 0
        # 会话内流转的三件套：generate_spec_docs 技能更新后写回，供同一轮
        # 后续工具调用（create_task）与后续轮次使用（修复：此前技能内写入
        # 的是 SkillContext 属性，局部变量不回写，同轮 create_task 恒报
        # 「还没有可执行的三件套规范文档」）
        session_specification = current_specification
        # 本轮 generate_spec_docs 最近一次执行是否失败（防误建任务）：失败
        # 置位、成功清除，同轮与后续轮的 create_task 据此拒绝创建——防止
        # LLM 被误导后基于旧草稿建任务、派发必然失败
        spec_generation_failed = False
        # 本轮工具轨迹（ADR-040）：assistant(tool_calls) + tool × N，
        # 经内部事件交端点按序落库，下一轮随历史回流 LLM
        trajectory: list[dict[str, Any]] = []
        # 上下文压缩（ADR-041）
        compaction_service = ContextCompactionService()
        model_window = int(getattr(client._config, "context_window", 65536))  # noqa: SLF001
        # 估算密度（ADR-041 修订 2026-09-27）：优先用上一轮真实 usage 校准值，
        # 未经校准时回退种子常数；本轮判定基准在判定前确定，不做轮内漂移
        chars_per_token = estimate_chars_per_token(compaction)
        payload_chars = 0

        async def _finalize_prompt() -> None:
            """环境快照落库与变化增量（ADR-042）。

            必须在每轮「最终 prompt 定稿前」调用（压缩重投影后需再调一次）：
            增量消息要出现在最终 prompt_messages 尾部，否则压缩重投影会把
            先前追加的增量丢弃。多次调用幂等（快照比对以实时块为准）。
            """
            if building_session is None:
                return
            if not environment_snapshot or not environment_snapshot.get("block"):
                # 首轮：Baseline 快照落库（system 已使用当前环境块）
                building_session.context_environment = {
                    "block": current_env,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
                await self._session.commit()
            elif environment_snapshot["block"] != current_env:
                # 环境变化（跨小时/节点上下线）：尾部追加增量并更新快照
                prompt_messages.append(build_environment_update_message(current_env))
                building_session.context_environment = {
                    "block": current_env,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
                await self._session.commit()
                logger.info(
                    "environment_update_injected",
                    extra={"event": "environment_update_injected"},
                )

        await _finalize_prompt()

        if building_session is not None:
            # 预压判定（ADR-041 修订 2026-09-27）：度量对象是**实际要发出的请求**
            # （定稿后的 prompt_messages），不是会话原始 messages——压缩只改
            # context_compaction 不改 messages，用原始历史判定会让估算在压缩后
            # 不回落，导致越线后每轮重复判定+重复提示（排查记录 §三）
            system_text = prompt_messages[0]["content"]
            payload_chars = measure_payload_chars(prompt_messages, tools, system_text)
            if should_compact_now(
                messages,
                tools,
                model_window,
                system_text,
                compaction,
                chars_per_token,
                pressure_messages=prompt_messages,
            ):
                # 事件仅在「确将推进」时下发（compaction 已覆盖本次 head 时不下发）
                yield {"event": "context_compacting", "data": {}}
                did_pre_compact, compacted_state = await self._compact_and_reproject(
                    compaction_service,
                    session_id=session_id,
                    building_session=building_session,
                    messages=messages,
                    current_specification=current_specification,
                    skills=skills,
                    disabled_skills=disabled_skills,
                    tools=tools,
                    client=client,
                    system_text=system_text,
                    force=False,
                )
                if did_pre_compact:
                    prompt_messages = self._build_messages(
                        messages,
                        current_specification,
                        skills,
                        disabled_skills,
                        compacted_state,
                        environment_block=env_for_system,
                        todos_block=todos_block,
                    )
                    await _finalize_prompt()
                    # 重投影后请求体变化，字符基准同步刷新（校准用）
                    payload_chars = measure_payload_chars(
                        prompt_messages, tools, prompt_messages[0]["content"]
                    )
                    yield {
                        "event": "context_compacted",
                        "data": {
                            "compacted": True,
                            "message_count": (compacted_state or {}).get("message_count"),
                        },
                    }
                compaction = compacted_state
            elif compaction:
                # 已有压缩点且本次无新增：仍告知前端（compacted=false），
                # 但不提示「正在压缩」（消除每轮空转提示）
                yield {
                    "event": "context_compacted",
                    "data": {
                        "compacted": False,
                        "message_count": compaction.get("message_count"),
                    },
                }

        logger.info(
            "orchestrator_chat_started",
            extra={
                "event": "orchestrator_chat_started",
                "message_count": len(messages),
                "enabled_skills": [skill.name for skill in skills],
                "disabled_skills": [skill.name for skill in disabled_skills],
                "model": model,
            },
        )
        try:
            # 降级链（ADR-044）：仅本轮对话首次 LLM 流式调用失败（重试耗尽后）
            # 时降级到其他已配置模型，整轮最多一次；后续工具轮失败不降级
            # （中途断流时前端已收到部分输出，换模型重跑会造成内容重复）
            first_llm_call = True
            fallback_tried = False
            fallback_client: DeepSeekClient | None = None
            # Steering（ADR-045）：for→while 手动计数，边界提升插话时
            # rounds_used = 0 重置步数配额（tilacode 语义）；队列已在
            # meta 前注册（open_stream）
            rounds_used = 0
            while rounds_used < max_rounds:
                rounds_used += 1
                # overflow 兜底（ADR-041）：provider 报 context overflow 时
                # 压缩后重跑本轮，仅重试一次
                overflow_retried = False
                while True:
                    flt = _DsmlStreamFilter()
                    content_parts: list[str] = []
                    # 推理内容累积（ADR-056 决策 D4）：写入 assistant 条目供
                    # 带 tools 的后续请求回传（reasoning_content）
                    reasoning_parts: list[str] = []
                    message: dict[str, Any] = {}
                    usage: dict[str, Any] | None = None
                    llm_started = time.monotonic()
                    try:
                        # 真流式（ADR-040）：思考/正文增量实时下发，tool_calls
                        # 增量由客户端按 index 拼装，流结束拿到完整 assistant message
                        async for event in client.chat_completion_stream_raw(
                            prompt_messages,
                            tools=tools,
                            thinking=thinking_enabled,
                        ):
                            if event["type"] == "reasoning_delta":
                                # ADR-056 决策 D4：累积推理内容——既实时下发前端，
                                # 也写入本轮 assistant 条目，供带 tools 的后续请求
                                # 作为 reasoning_content 回传（官方硬要求）
                                reasoning_parts.append(event["delta"])
                                yield {"event": "thinking", "data": {"delta": event["delta"]}}
                            elif event["type"] == "content_delta":
                                content_parts.append(event["delta"])
                                emitted = flt.feed(event["delta"])
                                if emitted:
                                    yield {"event": "chunk", "data": {"delta": emitted}}
                            elif event["type"] == "message":
                                message = event["message"]
                                usage = event.get("usage")
                        # LLM 调用链路采集（ADR-047）：成功轮记录耗时与产出形态
                        await tool_call_log_service.record(
                            kind="llm_call",
                            name=model,
                            status="success",
                            session_id=session_id,
                            duration_ms=int((time.monotonic() - llm_started) * 1000),
                            result_summary={
                                "tool_calls": len(message.get("tool_calls") or []),
                                "reply_chars": len("".join(content_parts)),
                            },
                        )
                        # 计量校准（ADR-041 修订 2026-09-27）：拿到真实 usage 后
                        # 观测密度并平滑回写，供下一轮判定使用
                        await self._calibrate_session_density(
                            building_session, payload_chars, usage
                        )
                    except DeepSeekAPIError as exc:
                        if (
                            not overflow_retried
                            and building_session is not None
                            and is_context_overflow_error(exc.status_code, exc.message)
                        ):
                            overflow_retried = True
                            yield {"event": "context_compacting", "data": {}}
                            did_compact, compaction = await self._compact_and_reproject(
                                compaction_service,
                                session_id=session_id,
                                building_session=building_session,
                                messages=messages,
                                current_specification=current_specification,
                                skills=skills,
                                disabled_skills=disabled_skills,
                                tools=tools,
                                client=client,
                                system_text=prompt_messages[0]["content"],
                                force=True,
                                log_name="overflow_compact",
                            )
                            yield {
                                "event": "context_compacted",
                                "data": {
                                    "compacted": did_compact,
                                    "message_count": (compaction or {}).get("message_count"),
                                },
                            }
                            if did_compact:
                                prompt_messages = self._build_messages(
                                    messages,
                                    current_specification,
                                    skills,
                                    disabled_skills,
                                    compaction,
                                    environment_block=env_for_system,
                                    todos_block=todos_block,
                                )
                                await _finalize_prompt()
                                continue
                            # 防重复跳过（无新摘要）但压缩点已存在（预压缩刚落库、
                            # prompt 已投影）：overflow 重跑本轮依然成立（ADR-041 修订）
                            if building_session.context_compaction is not None:
                                prompt_messages = self._build_messages(
                                    messages,
                                    current_specification,
                                    skills,
                                    disabled_skills,
                                    building_session.context_compaction,
                                    environment_block=env_for_system,
                                    todos_block=todos_block,
                                )
                                await _finalize_prompt()
                                continue
                        # 降级链（ADR-044）：首次流式调用重试耗尽仍失败且配置了
                        # 其他模型时，换 fallback client 重跑本轮
                        if (
                            first_llm_call
                            and not fallback_tried
                            and exc.status_code in RETRYABLE_STATUS_CODES
                        ):
                            candidate = await self._acquire_fallback_client(client)
                            if candidate is not None:
                                fallback_tried = True
                                fallback_client = candidate
                                yield {
                                    "event": "warning",
                                    "data": {
                                        "message": (
                                            f"模型 {model} 持续不可用，"
                                            f"已降级到 {candidate._config.model} 继续"
                                        )
                                    },
                                }
                                client = candidate
                                model = candidate._config.model  # noqa: SLF001
                                model_window = int(
                                    getattr(client._config, "context_window", 65536)  # noqa: SLF001
                                )
                                yield {
                                    "event": "meta",
                                    "data": {"model": model, "mode": mode},
                                }
                                continue
                        # LLM 调用链路采集（ADR-047）：重试耗尽仍失败
                        await tool_call_log_service.record(
                            kind="llm_call",
                            name=model,
                            status="failed",
                            session_id=session_id,
                            duration_ms=int((time.monotonic() - llm_started) * 1000),
                            error=exc.message,
                        )
                        raise
                    break
                first_llm_call = False

                # context_usage（ADR-041）：真实 usage 优先，缺失时估算回落
                used_tokens = (usage or {}).get("prompt_tokens") or estimate_messages_tokens(
                    prompt_messages, tools
                )
                yield {
                    "event": "context_usage",
                    "data": {
                        "used_tokens": used_tokens,
                        "window_tokens": model_window,
                        "percent": round(used_tokens / model_window * 100, 1)
                        if model_window
                        else None,
                    },
                }
                tail = flt.flush()
                if tail:
                    yield {"event": "chunk", "data": {"delta": tail}}

                tool_calls = message.get("tool_calls")
                if not tool_calls:
                    # 收尾前 drain（ADR-045）：有排队插话则先补上本轮 assistant
                    # 正文再提升插话，延续对话（模型基于插话继续回答），插话不丢
                    steered = steering_service.drain(session_id) if session_id else []
                    await _log_steering(session_id, steered)
                    if steered:
                        rounds_used = 0
                        final_content = _strip_fake_tool_syntax("".join(content_parts))
                        if final_content:
                            prompt_messages.append({"role": "assistant", "content": final_content})
                        async for event in _apply_steered_messages(steered, prompt_messages):
                            yield event
                        continue
                    # 最终回答已实时推送；含伪工具调用标记时流式修正重试
                    if flt.contaminated:
                        async for event in _stream_fake_tool_correction(
                            client,
                            prompt_messages,
                            "".join(content_parts),
                            thinking=thinking_enabled,
                        ):
                            yield event
                    # ADR-050 收尾总结提示（通道 a）：会话有 pending 经验条目
                    # 时引导总结；用户同意后 AI 经 experience_recall/resolve 闭环
                    pending_hint = await _experience_pending_hint(self._session, session_id)
                    if pending_hint:
                        yield {"event": "chunk", "data": {"delta": pending_hint}}
                    yield {"event": "done", "data": {}}
                    logger.info(
                        "orchestrator_chat_completed",
                        extra={
                            "event": "orchestrator_chat_completed",
                            "reply_length": len("".join(content_parts)),
                        },
                    )
                    return

                # 工具轮：assistant(tool_calls) 进 prompt 与轨迹（content
                # 原样回流，DSML 标记剥离后存储；None 归一化为空串，
                # ChatMessage.content 为 str 类型，落库/回流均安全）
                assistant_entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": _strip_fake_tool_syntax("".join(content_parts)),
                    "tool_calls": tool_calls,
                }
                # ADR-056 决策 D4（路线 B）：推理内容写入 assistant 条目。
                # 两处都要有，缺一不可：
                #   ① prompt_messages 直接续行（同一轮对话内，不重走 _build_messages）
                #      → 必须当场带 reasoning_content，否则本轮后续请求缺字段；
                #   ② `thinking` 供 _build_messages 在**下一条 HTTP 请求**里
                #      重建历史时回传 reasoning_content（官方硬要求）。
                reasoning_text = "".join(reasoning_parts)
                if reasoning_text:
                    # reasoning_content 进历史与 mode 无关（ADR-056 D4 硬要求：
                    # 带 tools 的请求历史必须回传推理内容，否则 provider 400）；
                    # mode/thinking 开关只控制本轮是否要求模型输出推理。
                    assistant_entry["thinking"] = reasoning_text
                    assistant_entry["reasoning_content"] = reasoning_text
                prompt_messages.append(assistant_entry)
                trajectory.append(dict(assistant_entry))

                # Doom Loop 检测（ADR-043）：连续 threshold 轮完全相同的调用
                # （同名+同参数，规范化签名）→ 本轮不再执行，补「未执行」tool
                # 消息（协议合法 + 审计）后强制收尾，避免烧满步数上限
                recent_signatures.append(_tool_calls_signature(tool_calls))
                if (
                    len(recent_signatures) >= doom_threshold
                    and len(set(recent_signatures[-doom_threshold:])) == 1
                ):
                    for tool_call in tool_calls:
                        seq += 1
                        step_id = str(uuid.uuid4())
                        function = tool_call.get("function", {})
                        skill_name = str(function.get("name", ""))
                        yield {
                            "event": "step_started",
                            "data": {
                                "step_id": step_id,
                                "seq": seq,
                                "skill_name": skill_name,
                                "skill_display_name": skill_name,
                                "args_summary": str(function.get("arguments", ""))[
                                    :ARGS_SUMMARY_MAX
                                ],
                            },
                        }
                        yield {
                            "event": "step_finished",
                            "data": {
                                "step_id": step_id,
                                "seq": seq,
                                "status": "failed",
                                "result_summary": "检测到连续重复调用，未执行",
                                "duration_ms": 0,
                            },
                        }
                        skipped_message = {
                            "role": "tool",
                            "tool_call_id": str(tool_call.get("id", "")),
                            "content": "检测到连续重复的相同调用，本次未执行。",
                        }
                        # doom 跳过链路采集（ADR-047）：未执行的调用也要可溯源
                        await tool_call_log_service.record(
                            kind="skill_call",
                            name=skill_name,
                            status="failed",
                            session_id=session_id,
                            args_summary=str(function.get("arguments", ""))[:ARGS_SUMMARY_MAX],
                            error="检测到连续重复调用，未执行（doom loop）",
                        )
                        prompt_messages.append(skipped_message)
                        trajectory.append(dict(skipped_message))
                    yield {
                        "event": _TRAJECTORY_EVENT,
                        "data": {"messages": [dict(m) for m in trajectory]},
                    }
                    # doom 收尾前 drain（ADR-045）：插话提升进收尾 prompt，
                    # 模型强制收尾时参考最新修正
                    steered = steering_service.drain(session_id) if session_id else []
                    await _log_steering(session_id, steered)
                    if steered:
                        async for event in _apply_steered_messages(steered, prompt_messages):
                            yield event
                    yield {
                        "event": "warning",
                        "data": {"message": "检测到重复的工具调用，已停止执行并基于已有结果作答"},
                    }
                    prompt_messages.append(
                        {"role": "system", "content": ORCHESTRATOR_DOOM_LOOP_PROMPT}
                    )
                    async for event in _stream_plain_answer(
                        client, prompt_messages, thinking=thinking_enabled
                    ):
                        yield event
                    return

                for tool_call in tool_calls:
                    seq += 1
                    tool_message: dict[str, Any] | None = None
                    skill_name = str(tool_call.get("function", {}).get("name", ""))
                    context = SkillContext(
                        messages=messages,
                        current_specification=session_specification,
                        mode=mode,
                        session_id=session_id,
                        parent_task_id=parent_task_id,
                        output_dir=output_dir,
                        spec_generation_failed=spec_generation_failed,
                    )
                    async for event in _run_tool_call(
                        session=self._session,
                        tool_call=tool_call,
                        context=context,
                        seq=seq,
                    ):
                        if event.get("event") == _TOOL_MESSAGE_EVENT:
                            tool_message = event.get("data")
                            continue
                        if (
                            event.get("event") == "step_finished"
                            and skill_name == "generate_spec_docs"
                        ):
                            # 失败置位 / 成功清除：同轮后续 create_task 读取
                            spec_generation_failed = (
                                event.get("data", {}).get("status") != "success"
                            )
                        yield event
                    if tool_message is not None:
                        prompt_messages.append(tool_message)
                        trajectory.append(dict(tool_message))
                    # 技能更新了三件套（generate_spec_docs）：写回并在后续
                    # 提示词中注入最新规范，保证同轮 create_task 与最终
                    # 回答都能感知
                    if context.current_specification is not session_specification:
                        session_specification = context.current_specification
                        if session_specification:
                            spec_entry = {
                                "role": "system",
                                "content": ORCHESTRATOR_SPEC_CONTEXT_TEMPLATE.format(
                                    spec_json=json.dumps(session_specification, ensure_ascii=False)
                                ),
                            }
                            prompt_messages.append(spec_entry)
                # 内部事件：本轮轨迹交端点落库（不透传前端）
                yield {
                    "event": _TRAJECTORY_EVENT,
                    "data": {"messages": [dict(m) for m in trajectory]},
                }
                # 进度检测（ADR-058 D2）：本轮是否引入了此前从未返回过的技能。
                # 覆盖 Doom Loop 未覆盖的形态——换了参数但反复查同一批工具且无新结论。
                # 技能失败也算「已返回」（失败是信息），故不区分状态。
                current_round_skills = {
                    str(call.get("function", {}).get("name", "")) for call in tool_calls
                }
                new_skills = current_round_skills - seen_skill_names
                seen_skill_names |= current_round_skills
                if new_skills:
                    no_progress_streak = 0
                else:
                    no_progress_streak += 1
                    if no_progress_streak >= no_progress_threshold:
                        logger.warning(
                            "orchestrator_no_progress_stop",
                            extra={
                                "event": "orchestrator_no_progress_stop",
                                "session_id": session_id,
                                "rounds_used": rounds_used,
                                "no_progress_streak": no_progress_streak,
                                "skills": sorted(current_round_skills),
                            },
                        )
                        # 收尾前 drain（ADR-045）：插话提升进收尾 prompt
                        if session_id:
                            steered = steering_service.drain(session_id)
                            await _log_steering(session_id, steered)
                            if steered:
                                async for event in _apply_steered_messages(
                                    steered, prompt_messages
                                ):
                                    yield event
                        yield {
                            "event": "warning",
                            "data": {
                                "message": (
                                    f"已连续 {no_progress_streak} 轮没有获得新信息，"
                                    "已停止重复查询并基于现有结果作答"
                                )
                            },
                        }
                        prompt_messages.append(
                            {"role": "system", "content": ORCHESTRATOR_NO_PROGRESS_PROMPT}
                        )
                        async for event in _stream_plain_answer(
                            client, prompt_messages, thinking=thinking_enabled
                        ):
                            yield event
                        return
                # 逼近上限警示（ADR-058 D3）：让用户知道「不是卡住，而是在做长任务」
                if not rounds_warning_emitted and rounds_used >= warning_at:
                    rounds_warning_emitted = True
                    yield {
                        "event": "warning",
                        "data": {
                            "message": (
                                f"本轮对话已使用 {rounds_used}/{max_rounds} 轮工具调用，"
                                "任务较复杂，仍在继续推进"
                            )
                        },
                    }
                # 工具轮边界 drain（ADR-045，tilacode Provider Turn 边界）：
                # 提升排队插话，下一轮流式调用即可感知修正
                if session_id:
                    steered = steering_service.drain(session_id)
                    await _log_steering(session_id, steered)
                    if steered:
                        rounds_used = 0
                        async for event in _apply_steered_messages(steered, prompt_messages):
                            yield event
            # 达到最大轮数：三重禁用强制纯文本收尾（ADR-040 对齐 tilacode
            # max-steps——不携带 tools + tool_choice="none" + 收尾提示）
            # 收尾前 drain（ADR-045）：插话提升进收尾 prompt
            logger.warning(
                "orchestrator_max_rounds_reached",
                extra={
                    "event": "orchestrator_max_rounds_reached",
                    "session_id": session_id,
                    "rounds_used": rounds_used,
                    "max_rounds": max_rounds,
                },
            )
            if session_id:
                steered = steering_service.drain(session_id)
                await _log_steering(session_id, steered)
                if steered:
                    async for event in _apply_steered_messages(steered, prompt_messages):
                        yield event
            prompt_messages.append({"role": "system", "content": ORCHESTRATOR_FORCE_FINAL_PROMPT})
            async for event in _stream_plain_answer(
                client, prompt_messages, thinking=thinking_enabled
            ):
                yield event
        except asyncio.CancelledError:
            # 用户中断（点「停止」/关闭页面）：流被取消。把该会话 in_progress
            # 待办重置回 pending——终止后不应残留「进行中」（2026-10-07 用户实测
            # 反馈：终止任务后待办还显示进行中）。旁路失败不影响取消传播。
            if session_id:
                try:
                    from app.services import orchestrator_todo_service as _todo_service

                    await _todo_service.reset_in_progress_for_chat_session(
                        self._session, uuid.UUID(str(session_id))
                    )
                except Exception:  # noqa: BLE001 — 清理旁路，失败仅告警
                    logger.warning(
                        "orchestrator_todos_in_progress_reset_failed",
                        extra={
                            "event": "orchestrator_todos_in_progress_reset_failed",
                            "session_id": session_id,
                        },
                        exc_info=True,
                    )
            raise
        except DeepSeekAPIError as exc:
            logger.warning(
                "orchestrator_deepseek_api_error",
                extra={
                    "event": "orchestrator_deepseek_api_error",
                    "status_code": exc.status_code,
                    "reason": exc.message,
                },
                exc_info=True,
            )
            yield {"event": "error", "data": {"message": exc.message}}
            yield {"event": "done", "data": {}}
        except Exception:
            logger.exception("orchestrator_chat_failed")
            yield {"event": "error", "data": {"message": "对话处理异常，请稍后重试"}}
            yield {"event": "done", "data": {}}
        finally:
            # 降级 client 为本轮临时构造，用完即关（主 client 由创建方管理）
            if fallback_client is not None:
                await fallback_client.close()
            # Steering（ADR-045）：流结束注销队列（残留兜底丢弃）
            if session_id:
                steering_service.close_stream(session_id)

    async def _compact_and_reproject(
        self,
        compaction_service: ContextCompactionService,
        *,
        session_id: str | None,
        building_session: TaskBuildingSession,
        messages: list[dict[str, Any]],
        current_specification: dict[str, Any] | None,
        skills: list[Any],
        disabled_skills: list[Any],
        tools: list[dict[str, Any]] | None,
        client: DeepSeekClient,
        system_text: str,
        force: bool,
        log_name: str = "pre_compact",
    ) -> tuple[bool, dict[str, Any] | None]:
        """执行一次压缩并记录链路事件（ADR-041 修订 2026-09-27）。

        消除预压与 overflow 兜底两条路径的重复实现：两条路径共用同一份
        「调用压缩 → 记录 compaction 事件 → 返回最新压缩点」逻辑，避免两边
        行为漂移。重投影由调用方完成（两边的重投影时机与后续步骤不同）。

        Args:
            force: True 表示 overflow 兜底（跳过预压判定，强制压缩）。
            system_text: **真实** system prompt 正文。必须逐字传入（而非重新拼装），
                因为摘要调用要用它重放真前缀以复用 provider KV 缓存
                （ADR-054 决策 B）；同时它也是压缩判定的体积口径。
            log_name: 链路事件的 name（pre_compact / overflow_compact）。

        Returns:
            (是否真的执行了压缩, 最新压缩点)。
        """
        did_compact, compaction = await compaction_service.compact_if_needed(
            self._session,
            building_session,
            messages,
            tools,
            client,
            force=force,
            system_text=system_text,
        )
        await tool_call_log_service.record(
            kind="compaction",
            name=log_name,
            session_id=session_id,
            result_summary={
                "compacted": did_compact,
                "message_count": (compaction or {}).get("message_count"),
            },
        )
        return did_compact, compaction

    async def _calibrate_session_density(
        self,
        building_session: TaskBuildingSession | None,
        payload_chars: int,
        usage: dict[str, Any] | None,
    ) -> None:
        """按 provider 真实 usage 校准估算密度并回写会话（ADR-041 修订 2026-09-27）。

        仅在会话已有压缩点时回写：`context_compaction` 为空说明该会话尚未压缩，
        以 `{est_chars_per_token}` 初始化会创建只有校准键的压缩点对象，
        语义上等效于「无压缩点」（`project_messages` 会安全忽略），但会让
        `context_compaction IS NOT NULL` 的既有判读产生歧义，故不做。

        任何失败仅告警，绝不影响对话主链路。
        """
        if building_session is None or not usage:
            return
        current = building_session.context_compaction
        if not current:
            return
        real_prompt_tokens = usage.get("prompt_tokens")
        try:
            next_ratio = calibrate_chars_per_token(
                payload_chars,
                real_prompt_tokens,
                estimate_chars_per_token(current),
            )
        except (AttributeError, TypeError, ValueError):
            logger.warning(
                "context_calibration_failed",
                extra={"event": "context_calibration_failed"},
                exc_info=True,
            )
            return
        if next_ratio is None:
            return
        building_session.context_compaction = {
            **current,
            "est_chars_per_token": next_ratio,
            "est_sample_at": datetime.now(UTC).isoformat(),
        }
        try:
            await self._session.commit()
        except Exception:
            logger.warning(
                "context_calibration_persist_failed",
                extra={"event": "context_calibration_persist_failed"},
                exc_info=True,
            )
            return
        logger.info(
            "context_calibration_updated",
            extra={
                "event": "context_calibration_updated",
                "est_chars_per_token": round(next_ratio, 3),
                "measured_chars": payload_chars,
                "real_prompt_tokens": real_prompt_tokens,
            },
        )

    async def _acquire_fallback_client(self, current: DeepSeekClient) -> DeepSeekClient | None:
        """选择降级模型并构造新客户端（ADR-044）；无可用候选返回 None。

        候选 = llm_providers 全表排除使用中模型，按 created_at 升序取第一个。
        """
        rows = await LLMProviderService().list_models(self._session)
        current_model = current._config.model  # noqa: SLF001
        candidates = [row for row in rows if row.model != current_model]
        if not candidates:
            return None
        row = candidates[0]
        config = LLMProviderConfig(
            provider=row.provider,
            api_key=row.api_key,
            base_url=row.base_url,
            model=row.model,
            supports_vision=bool(row.supports_vision),
            context_window=int(row.context_window),
        )
        logger.warning(
            "orchestrator_model_fallback",
            extra={
                "event": "orchestrator_model_fallback",
                "from_model": current_model,
                "to_model": row.model,
            },
        )
        return DeepSeekClient(config, scene="orchestrator")


class _DsmlStreamFilter:
    """真流式下的伪工具调用（DSML）标记过滤器（ADR-040）。

    正文增量实时下发的同时检测伪工具调用标记：标记可能跨增量分片到达，
    保留一个可能构成标记前缀的尾部缓冲，确认安全后才发出；一旦命中完整
    标记则置 ``contaminated``，后续内容全部吞掉（由调用方走修正重试）。
    """

    def __init__(self) -> None:
        self._buffer = ""
        self.contaminated = False

    def feed(self, delta: str) -> str:
        """喂入一个增量，返回可安全发出的文本。"""
        if self.contaminated:
            return ""
        self._buffer += delta
        for marker in _FAKE_TOOL_MARKERS:
            if marker in self._buffer:
                self.contaminated = True
                self._buffer = ""
                return ""
        # 尾部可能是任一标记的前缀：扣留待后续增量确认
        keep = 0
        for marker in _FAKE_TOOL_MARKERS:
            for k in range(1, len(marker)):
                if self._buffer.endswith(marker[:k]):
                    keep = max(keep, k)
        if keep >= len(self._buffer):
            return ""
        emitted = self._buffer[: len(self._buffer) - keep]
        self._buffer = self._buffer[len(self._buffer) - keep :]
        return emitted

    def flush(self) -> str:
        """流结束时冲刷缓冲；污染状态下不再输出任何内容。"""
        rest = "" if self.contaminated else self._buffer
        self._buffer = ""
        return rest


async def _stream_fake_tool_correction(
    client: DeepSeekClient,
    prompt_messages: list[dict[str, Any]],
    contaminated_content: str,
    thinking: bool | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """伪工具调用标记的流式修正重试（ADR-040）。

    追加修正提示做**一次**不带 tools 的流式重试，让模型改用自然语言说明
    技能不可用；重试内容经同一过滤器实时下发，仍含标记则剥离标记后一次性
    兜底输出（污染轮已发出的少量前缀无法撤回，属可接受的罕见路径）。

    Args:
        thinking: 思考模式开关（ADR-056 D3），与主链路保持一致。
    """
    logger.warning(
        "orchestrator_fake_tool_syntax_detected",
        extra={
            "event": "orchestrator_fake_tool_syntax_detected",
            "content_snippet": contaminated_content[:200],
        },
    )
    retry_messages = [
        *prompt_messages,
        {"role": "assistant", "content": contaminated_content},
        {"role": "system", "content": ORCHESTRATOR_FAKE_TOOL_CORRECTION},
    ]
    flt = _DsmlStreamFilter()
    retry_parts: list[str] = []
    try:
        # 不带 tools：修正轮强制文本回答，避免再次触发真实工具调用
        async for event in client.chat_completion_stream_raw(retry_messages, thinking=thinking):
            if event["type"] == "content_delta":
                retry_parts.append(event["delta"])
                emitted = flt.feed(event["delta"])
                if emitted:
                    yield {"event": "chunk", "data": {"delta": emitted}}
    except DeepSeekAPIError:
        logger.warning(
            "orchestrator_fake_tool_correction_failed",
            extra={"event": "orchestrator_fake_tool_correction_failed"},
            exc_info=True,
        )
        return
    tail = flt.flush()
    if flt.contaminated:
        cleaned = _strip_fake_tool_syntax("".join(retry_parts))
        if cleaned:
            yield {"event": "chunk", "data": {"delta": cleaned}}
    elif tail:
        yield {"event": "chunk", "data": {"delta": tail}}


async def _stream_plain_answer(
    client: DeepSeekClient,
    prompt_messages: list[dict[str, Any]],
    thinking: bool | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """流式下发纯文本回答（达最大轮数后的三重禁用收尾轮，ADR-040）。

    不携带 tools 且 ``tool_choice="none"``，模型无法再发起工具调用；
    文本经 DSML 过滤器实时下发，污染时剥离标记兜底。

    Args:
        thinking: 思考模式开关（ADR-056 D3），与主链路保持一致。
    """
    flt = _DsmlStreamFilter()
    parts: list[str] = []
    async for event in client.chat_completion_stream_raw(
        prompt_messages, tool_choice="none", thinking=thinking
    ):
        if event["type"] == "reasoning_delta":
            yield {"event": "thinking", "data": {"delta": event["delta"]}}
        elif event["type"] == "content_delta":
            parts.append(event["delta"])
            emitted = flt.feed(event["delta"])
            if emitted:
                yield {"event": "chunk", "data": {"delta": emitted}}
    tail = flt.flush()
    if flt.contaminated:
        cleaned = _strip_fake_tool_syntax("".join(parts))
        if cleaned:
            yield {"event": "chunk", "data": {"delta": cleaned}}
    elif tail:
        yield {"event": "chunk", "data": {"delta": tail}}
    yield {"event": "done", "data": {}}
    logger.info(
        "orchestrator_chat_completed",
        extra={"event": "orchestrator_chat_completed", "reply_length": len("".join(parts))},
    )


async def _run_tool_call(
    session: AsyncSession,
    tool_call: dict[str, Any],
    context: SkillContext,
    seq: int,
) -> AsyncGenerator[dict[str, Any], None]:
    """执行单个工具调用：步骤事件 + 技能事件转发 + 结果回传消息。

    事件序列：``step_started`` → 技能中间事件（原样转发）→
    ``step_finished`` → ``_tool_message``（内部事件，调用方消费后作为
    tool role 消息追加进 prompt，不透传前端）。
    """
    function = tool_call.get("function", {})
    skill_name = str(function.get("name", ""))
    arguments_raw = function.get("arguments") or "{}"
    step_id = str(uuid.uuid4())
    try:
        args = json.loads(arguments_raw) if isinstance(arguments_raw, str) else dict(arguments_raw)
    except json.JSONDecodeError:
        args = {}
    args_summary = json.dumps(args, ensure_ascii=False)[:ARGS_SUMMARY_MAX]
    skill = find_skill(skill_name)
    # 技能调用链路采集（ADR-047）：started 锚定时间线
    await tool_call_log_service.record(
        kind="skill_call",
        name=skill_name,
        status="started",
        session_id=context.session_id,
        args_summary=args_summary,
    )

    yield {
        "event": "step_started",
        "data": {
            "step_id": step_id,
            "seq": seq,
            "skill_name": skill_name,
            "skill_display_name": skill.display_name if skill else skill_name,
            "args_summary": args_summary,
        },
    }

    started = time.monotonic()
    result = SkillResult(success=False, summary="技能执行未返回结果")
    status = "failed"
    if skill is None:
        result = SkillResult(success=False, summary=f"未知技能：{skill_name}")
    else:
        # LLM 工具参数必须回填进执行上下文（修复：此前 context.args 恒为空，
        # 可选参数技能"恰好成功"掩盖问题，smart_dispatch 必填 task_id 直接触发
        # 「任务 ID 无效」）
        context.args = args
        # 技能超时（ADR-043）：共享 deadline 的逐事件限时——只在等待技能产出时
        # 计时（事件转发/挂起不占预算），超时取消挂起的 __anext__ 并转为失败
        # 结果；对话循环继续（模型收到失败 tool 消息可重试或换思路）
        budget = get_settings().skill_execution_timeout_seconds
        deadline = time.monotonic() + budget
        gen = skill.execute(session, context)
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                try:
                    event = await asyncio.wait_for(gen.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                if event.get("event") == RESULT_EVENT:
                    candidate = event.get("data")
                    if isinstance(candidate, SkillResult):
                        result = candidate
                        status = "success" if candidate.success else "failed"
                    continue
                yield event
        except TimeoutError:
            logger.warning(
                "skill_execution_timeout",
                extra={
                    "event": "skill_execution_timeout",
                    "skill": skill_name,
                    "timeout_seconds": budget,
                },
            )
            result = SkillResult(
                success=False,
                summary=f"技能执行超时（{int(budget)} 秒），已中止；可稍后重试或换个说法",
            )
            status = "failed"
            with contextlib.suppress(Exception):
                # 生成器清理失败不影响失败结果回传
                await asyncio.wait_for(gen.aclose(), timeout=5)
            # 丢弃技能未提交的事务残留，防脏状态污染后续轮次（ADR-043）
            await session.rollback()
    duration_ms = int((time.monotonic() - started) * 1000)
    # 技能结果链路采集（ADR-047）：成功/失败（含超时与 doom 跳过路径）
    await tool_call_log_service.record(
        kind="skill_result",
        name=skill_name,
        status=status,
        session_id=context.session_id,
        result_summary=result.summary[:RESULT_SUMMARY_MAX],
        error=result.summary if status != "success" else None,
        duration_ms=duration_ms,
    )

    yield {
        "event": "step_finished",
        "data": {
            "step_id": step_id,
            "seq": seq,
            "skill_name": skill_name,
            "status": status,
            "result_summary": result.summary[:RESULT_SUMMARY_MAX],
            # 技能结构化返回（ADR-061 M5d）：todo_write 成功时带 task_id，
            # 前端据此把任务摘要面板对齐到待办实际写入的任务——会话绑定任务
            # 会随多次 create_task 漂移，仅靠 URL ?task= 参数会错位显示空清单
            **({"result_data": result.data} if result.data else {}),
            "duration_ms": duration_ms,
        },
    }
    # 协议要求：assistant 的每个 tool_call 必须有对应 tool 响应消息
    # （未知技能名也必须回传，否则下一轮 LLM 调用会 400）
    yield {
        "event": _TOOL_MESSAGE_EVENT,
        "data": {
            "role": "tool",
            "tool_call_id": tool_call.get("id", ""),
            "content": result.summary,
        },
    }

    logger.info(
        "orchestrator_skill_executed",
        extra={
            "event": "orchestrator_skill_executed",
            "skill_name": skill_name,
            "status": status,
            "duration_ms": duration_ms,
            "args_summary": args_summary,
        },
    )


async def _run_forced_spec(
    session: AsyncSession,
    messages: list[dict[str, str]],
    current_specification: dict[str, Any] | None,
    mode: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """``/spec`` 显式命令：确定性强制执行 generate_spec_docs（ADR-036）。

    不走 LLM 决策轮（省一次调用且结果确定）；复用 ``_run_tool_call`` 的
    完整事件协议（``step_started`` → 技能中间事件 → ``step_finished``），
    完成后把 ``SkillResult.summary`` 作为回复分片下发并结束本轮。技能经
    注册表直取，被禁用也强制执行（用户显式命令优先于技能开关）。

    Args:
        session: 异步数据库会话（技能执行用）。
        messages: 本轮对话消息（技能上下文输入）。
        current_specification: 当前会话三件套快照（增量更新基础）。
        mode: 对话模式。
    """
    logger.info(
        "orchestrator_spec_command_forced",
        extra={"event": "orchestrator_spec_command_forced"},
    )
    tool_call = {
        "id": f"call_{uuid.uuid4().hex}",
        "function": {"name": "generate_spec_docs", "arguments": "{}"},
    }
    context = SkillContext(
        messages=messages,
        current_specification=current_specification,
        mode=mode,
    )
    summary = "规范文档已生成"
    forwarded_chunk = False
    async for event in _run_tool_call(session=session, tool_call=tool_call, context=context, seq=1):
        if event.get("event") == _TOOL_MESSAGE_EVENT:
            data = event.get("data")
            if isinstance(data, dict) and data.get("content"):
                summary = str(data["content"])
            continue
        if event.get("event") == "chunk":
            forwarded_chunk = True
        yield event
    # 技能自身 chunk 已构成可见回复；仅在无任何转发 chunk（如失败）时
    # 以结果摘要兜底，避免正文重复
    if not forwarded_chunk:
        for index in range(0, len(summary), REPLY_CHUNK_SIZE):
            yield {
                "event": "chunk",
                "data": {"delta": summary[index : index + REPLY_CHUNK_SIZE]},
            }
    yield {"event": "done", "data": {}}
