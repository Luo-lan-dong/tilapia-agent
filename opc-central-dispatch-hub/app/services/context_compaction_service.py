"""上下文压缩服务（ADR-041）：token 估算、预压触发、五段锚定摘要、压缩投影。

设计对齐探查档案章节六（tilacode compaction.ts）的轻量复刻：

- **双轨计量**：chars/4 启发式估算（触发判定）+ provider usage 真实值（占用展示）；
- **原文保留**：压缩摘要写入 ``task_building_sessions.context_compaction``，
  ``messages`` 列原始历史永不修改删除；
- **按需回查**：模型通过 ``history_recall`` 技能检索原文，对冲多轮压缩累积失真；
- **投影**：``_build_messages`` 有压缩点时只投影其后消息，头部注入 checkpoint。
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.deepseek import SUMMARY_MAX_OUTPUT_TOKENS
from app.core.config import get_settings
from app.models.task_building_session import TaskBuildingSession

logger = logging.getLogger(__name__)

# 触发缓冲：窗口 − 输出上限 − 缓冲 = 压缩触发线（预留输出的安全余量）
COMPACTION_OUTPUT_RESERVE_TOKENS = 8192
# ADR-056（P2）：缓冲改为按窗口比例 + 上下限。该常量降级为**下限**——
# 窗口校正为 1M 后仍按固定 20000 计，会把触发线推到 97% 窗口，
# 单请求体积失控；按比例并封顶则兼顾「不早压」与「不无限膨胀」。
COMPACTION_BUFFER_TOKENS = 20000
# 缓冲上限与比例（ADR-056）
COMPACTION_BUFFER_MAX_TOKENS = 100000
COMPACTION_BUFFER_RATIO = 0.05
# 压缩时尾部保留的最近上下文（token）：对连贯性最重要，不参与摘要
COMPACTION_RECENT_KEEP_TOKENS = 8000
# 序列化时单条消息 content 截断
_COMPACT_SERIES_SNIPPET_MAX = 2000
# 中文场景 token 密度：DeepSeek 等主流模型中文约 1.5~2 字/token，
# chars/4（tilacode 英文启发式）会低估 1.3~2 倍，导致压缩触发/摘要推进
# 判定失真（实测：估算 3921 vs 真实 usage 5088）。取 chars/2 保守高估，
# 宁可提前压缩也不让占用失控（ADR-041 修订 2026-09-23）。
#
# ADR-041 修订 2026-09-27（P0）：该常数降级为**冷启动种子**。混合语料
# （工具回传以 JSON/英文为主）实测真实密度 2.48~2.54，按 2 计价净高估 1.66 倍，
# 触发线被折算到窗口的 34% 即触发。运行期改为按 provider 真实 usage 观测
# 密度并平滑（见 calibrate_chars_per_token / estimate_chars_per_token）。
CHARS_PER_TOKEN = 2

# 密度校准（ADR-041 修订 2026-09-27）：观测样本与夹取区间
CALIBRATION_MIN_PROMPT_TOKENS = 2000
CALIBRATION_ALPHA = 0.3
CALIBRATION_MIN_CHARS_PER_TOKEN = 1.5
CALIBRATION_MAX_CHARS_PER_TOKEN = 6.0

_SUMMARY_TEMPLATE = """You are summarizing a conversation checkpoint for an ops-dispatch assistant. Produce the summary in Chinese, in EXACTLY the following Markdown structure:

<template>
## Objective
## Important Details
## Work State
### Completed
### Active
### Blocked
## Next Move
## Relevant Tasks & Nodes
</template>

Rules:
- Keep every section, even when empty.
- Use terse bullets, not prose paragraphs.
- Preserve exact node names, task IDs, file paths, commands, error strings, metrics numbers, URLs, and identifiers verbatim.
- Do not mention the summary process or that context was compacted.
- The following is the earlier conversation (older, most important first), followed by the recent tail that is kept verbatim:{anchored}
"""

_CHECKPOINT_TEMPLATE = """<conversation-checkpoint>
<summary>
{summary}
</summary>
<recent-context>
以下为摘要之后的完整近期对话。如需更早对话中的细节（节点名、任务 ID、命令、结论等），
可调用 history_recall 技能检索原始历史记录。
</recent-context>
</conversation-checkpoint>"""

# 压缩指令（ADR-054 决策 B）：作为**最后一条 user 消息**追加在逐字重放的对话之后，
# 使辅助调用成为上一次真实请求的真前缀（复用 provider KV 缓存）。与原
# `_SUMMARY_TEMPLATE` 的差异：不再自带「你是摘要器」的合成 system 角色，
# 也不再重复贴对话文本 —— 对话已在上下文里。
_SUMMARY_INSTRUCTION_TEMPLATE = """以上是本次对话到当前为止的完整上下文。请作为一个压缩引擎（compaction engine），把它压缩成一份结构化检查点，使另一个模型能够无损接续当前工作。

严格按照下面的 Markdown 结构输出，保持每个小节及其顺序，用简洁的列表项而不是散文段落；某节没有内容时写「（无）」，不要省略任何小节：

## Objective
## Important Details
## Work State
### Completed
### Active
### Blocked
## Next Move
## Relevant Tasks & Nodes

规则：
- 用中文输出；保留节点名、任务 ID、文件路径、命令、错误串、数字指标、URL 等标识符原文照抄。
- 不要提及本次压缩或摘要过程本身。
- 不要调用任何工具，也不要输出除检查点正文以外的任何内容。{anchored}"""


def estimate_tokens(text: str, chars_per_token: int | float = CHARS_PER_TOKEN) -> int:
    """按字符密度估算 token 数。

    Args:
        text: 待估算文本。
        chars_per_token: 字符/token 密度；缺省为冷启动种子常数，
            运行期由 :func:`estimate_chars_per_token` 提供的校准值传入
            （ADR-041 修订 2026-09-27）。
    """
    return round(len(text) / chars_per_token)


def estimate_messages_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    system_text: str | None = None,
    chars_per_token: int | float = CHARS_PER_TOKEN,
) -> int:
    """估算一次请求的总 token 占用（system + messages + tools 定义的 JSON 体积）。

    对齐 tilacode ``Token.estimate(JSON.stringify({system, messages, tools}))``：
    system prompt（技能清单/环境块）是请求体积的大头，漏算会导致压缩判定失效。

    Args:
        messages: 实际要发出的消息列表（压缩场景应为投影后的结果）。
        tools: OpenAI tools 定义。
        system_text: system prompt 正文。
        chars_per_token: 字符/token 密度（校准值见 ADR-041 修订 2026-09-27）。
    """
    return round(measure_payload_chars(messages, tools, system_text) / chars_per_token)


def measure_payload_chars(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    system_text: str | None = None,
) -> int:
    """测量一次请求的规范化字符数（与 :func:`estimate_messages_tokens` 同口径）。

    校准路径必须与估算路径使用同一字符口径，否则观测密度与实际计价密度
    不是同一个量（ADR-041 修订 2026-09-27）。
    """
    payload: dict[str, Any] = {"messages": messages, "tools": tools or []}
    if system_text:
        payload["system"] = system_text
    return len(json.dumps(payload, ensure_ascii=False, default=str))


def calibrate_chars_per_token(
    measured_chars: int,
    real_prompt_tokens: int | None,
    previous: float | None = None,
) -> float | None:
    """按 provider 真实 usage 校准字符密度（ADR-041 修订 2026-09-27）。

    观测密度 ``measured_chars / real_prompt_tokens`` 经指数滑动平均平滑后
    夹在 ``[1.5, 6.0]``；样本过小（低于噪声门槛）时不校准。

    Args:
        measured_chars: 当轮请求体的规范化字符数（:func:`measure_payload_chars`）。
        real_prompt_tokens: provider 返回的真实 ``usage.prompt_tokens``。
        previous: 上一次校准密度；None 时以种子常数 :data:`CHARS_PER_TOKEN` 起算。

    Returns:
        新的密度估计；样本不可用（无 usage / 低于门槛 / 无字符）时返回
        ``previous`` 原值（保持既有校准不被噪声拉偏）。
    """
    if (
        not real_prompt_tokens
        or real_prompt_tokens < CALIBRATION_MIN_PROMPT_TOKENS
        or measured_chars <= 0
    ):
        return previous
    observed = measured_chars / real_prompt_tokens
    observed = max(
        CALIBRATION_MIN_CHARS_PER_TOKEN,
        min(CALIBRATION_MAX_CHARS_PER_TOKEN, observed),
    )
    base = previous if previous is not None else float(CHARS_PER_TOKEN)
    return base + CALIBRATION_ALPHA * (observed - base)


def estimate_chars_per_token(compaction: dict[str, Any] | None) -> float:
    """从会话压缩点读取校准密度；缺失/非法时回退种子常数。"""
    if not compaction:
        return float(CHARS_PER_TOKEN)
    raw = compaction.get("est_chars_per_token")
    if isinstance(raw, bool):
        return float(CHARS_PER_TOKEN)
    if isinstance(raw, int | float) and raw > 0:
        return max(
            CALIBRATION_MIN_CHARS_PER_TOKEN,
            min(CALIBRATION_MAX_CHARS_PER_TOKEN, float(raw)),
        )
    return float(CHARS_PER_TOKEN)


def should_compact_now(
    chat_messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    context_window: int,
    system_text: str | None = None,
    compaction: dict[str, Any] | None = None,
    chars_per_token: int | float = CHARS_PER_TOKEN,
    pressure_messages: list[dict[str, Any]] | None = None,
) -> bool:
    """本次压缩是否**确将推进**（越线 ∧ head 相对既有压缩点确有新增）。

    两个输入口径必须区分（ADR-041 修订 2026-09-27）：

    - **压力口径** ``pressure_messages``：实际要发出的请求（投影后的
      ``prompt_messages``）。压缩只改 ``context_compaction`` 不改原始历史，
      用原始历史测压力会让估算在压缩后不回落、越线后每轮重复判定；
      缺省回退 ``chat_messages``。
    - **切分口径** ``chat_messages``：会话原始历史。必须与
      :meth:`ContextCompactionService._generate_summary` 的防重复判定
      （``previous.message_count >= len(head)``）同源，否则本函数为真而摘要端
      跳过（或反之），事件与实际行为不一致。

    Returns:
        是否既越线、又确有可推进的新 head。判定为真但摘要端仍可能因摘要
        失败而跳过（失败路径不抛错，由调用方按 ``compacted`` 布尔呈现）。
    """
    measured = pressure_messages if pressure_messages is not None else chat_messages
    if not should_pre_compact(measured, tools, context_window, system_text, chars_per_token):
        return False
    head, _ = split_for_compaction(
        chat_messages, recent_keep_tokens=recent_keep_tokens_for(context_window)
    )
    if not head:
        return False
    previous = int((compaction or {}).get("message_count") or 0)
    return previous < len(head)


def compaction_buffer_tokens(context_window: int) -> int:
    """压缩安全缓冲：``max(20000, 窗口×5%)``，上限 100000（ADR-056）。

    窗口从 64K 校正为 1M 后，固定 20K 缓冲会让触发线贴近窗口上沿，
    单请求体积失控；按比例并设上下限兼顾「不早压」与「不无限膨胀」。
    """
    return min(
        COMPACTION_BUFFER_MAX_TOKENS,
        max(COMPACTION_BUFFER_TOKENS, int(context_window * COMPACTION_BUFFER_RATIO)),
    )


def compaction_threshold(context_window: int) -> int:
    """压缩触发线：窗口 − 输出预留 − 安全缓冲（下限 256，兼容极小测试窗口）。"""
    return max(
        256,
        context_window - COMPACTION_OUTPUT_RESERVE_TOKENS - compaction_buffer_tokens(context_window),
    )


def recent_keep_tokens_for(context_window: int) -> int:
    """按窗口自适应的 recent 保留量：大窗口用默认 8000，小窗口按 1/8 缩放。

    保留量超过窗口本身时切分永远为空（head 无内容可压缩），必须随窗口缩小。
    """
    return max(128, min(COMPACTION_RECENT_KEEP_TOKENS, context_window // 8))


def should_pre_compact(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    context_window: int,
    system_text: str | None = None,
    chars_per_token: int | float = CHARS_PER_TOKEN,
) -> bool:
    """预压判定：估算占用（含 system）超过触发线时返回 True。

    Args:
        chars_per_token: 字符/token 密度；调用方应传会话校准值
            （:func:`estimate_chars_per_token`），缺省为冷启动种子常数。
    """
    return estimate_messages_tokens(
        messages, tools, system_text, chars_per_token
    ) > compaction_threshold(context_window)


def is_context_overflow_error(status_code: int, message: str) -> bool:
    """识别 provider 的 context overflow 报错（兜底压缩触发条件）。"""
    if status_code != 400:
        return False
    lowered = message.lower()
    return any(kw in lowered for kw in ("context", "length", "too long", "maximum", "token"))


def _serialize_message(
    message: dict[str, Any], snippet_max: int | None = _COMPACT_SERIES_SNIPPET_MAX
) -> str:
    """把一条历史消息序列化为带前缀的摘要输入文本。

    ``snippet_max`` 为截断上限（默认 2000 字符）；传 ``None`` 不截断，
    用于切分时的真实成本估算。
    """
    role = message.get("role", "unknown")
    content = str(message.get("content") or "")
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        calls = ", ".join(str(tc.get("function", {}).get("name", "?")) for tc in tool_calls)
        content = f"{content} [调用技能: {calls}]" if content else f"[调用技能: {calls}]"
    if role == "tool":
        content = str(message.get("tool_call_id", "")) + " → " + content
    if snippet_max is not None and len(content) > snippet_max:
        content = content[:snippet_max] + f"...(截断，原文 {len(content)} 字符)"
    return f"[{role}]: {content}" if content else f"[{role}]: (empty)"


def _message_cost(message: dict[str, Any]) -> int:
    """按未截断序列化文本估算单条消息的真实 token 成本。"""
    return estimate_tokens(_serialize_message(message, snippet_max=None))


def split_for_compaction(
    messages: list[dict[str, Any]],
    recent_keep_tokens: int = COMPACTION_RECENT_KEEP_TOKENS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """切分压缩输入：尾部 recent 保留（不摘要），其余为 head（待摘要）。

    Returns:
        (head_messages, recent_messages)；head 为空（历史太短）时不执行压缩。
    """
    head_end = 0
    accumulated = 0
    for index in range(len(messages) - 1, -1, -1):
        cost = _message_cost(messages[index])
        if accumulated + cost > recent_keep_tokens:
            head_end = index + 1
            break
        accumulated += cost
    else:
        head_end = 0
    return messages[:head_end], messages[head_end:]


def build_summary_prompt(
    head_messages: list[dict[str, Any]],
    recent_messages: list[dict[str, Any]],
    previous_summary: str | None,
) -> str:
    """组装五段锚定摘要的 user prompt。"""
    anchored = ""
    if previous_summary:
        anchored = f"\n\n## Previous checkpoint summary (update/extend it, do not lose its facts):\n{previous_summary}"
    parts = [
        _SUMMARY_TEMPLATE.format(anchored=anchored),
        "\n---\n\nEarlier conversation (to summarize):",
    ]
    parts.extend(_serialize_message(m) for m in head_messages)
    if recent_messages:
        parts.append("\n---\n\nRecent tail (kept verbatim in context, for reference only):")
        parts.extend(_serialize_message(m) for m in recent_messages)
    return "\n".join(parts)


def build_summary_instruction(previous_summary: str | None = None) -> str:
    """构造压缩指令正文（作为最后一条 user 消息，ADR-054 决策 B）。

    与 :func:`build_summary_prompt` 的区别：本函数产出的是**尾部指令**——
    待摘要的对话已经在上下文里逐字重放过了，因此指令不再重复贴一遍对话文本，
    只描述任务与输出结构。这样辅助调用才能成为上一次真实请求的**真前缀**，
    复用 provider KV 缓存（只有本指令与输出不计入命中）。

    参考实现 `借鉴/deepseek-harness-master` 的 `COMPACTION_INSTRUCTION` 定位
    （`packages/compaction/compaction-basic/src/summarizer.ts:25-31`）。
    """
    anchored = ""
    if previous_summary:
        anchored = f"\n\n## 已有检查点摘要（增量更新，不要丢失其中的事实）\n{previous_summary}"
    return _SUMMARY_INSTRUCTION_TEMPLATE.format(anchored=anchored)


def build_checkpoint_message(summary_text: str) -> dict[str, Any]:
    """构造投影头部的压缩检查点消息（user 角色）。"""
    return {
        "role": "user",
        "content": _CHECKPOINT_TEMPLATE.format(summary=summary_text),
    }


def safe_cutoff(messages: list[dict[str, Any]], cutoff: int) -> int:
    """把压缩切点校正到「不切分工具轮」的安全边界（ADR-054 决策 A）。

    压缩切点若落在 ``role: "tool"`` 的消息上，该工具消息的父级
    ``assistant(tool_calls)`` 会被切在压缩点之前，投影序列随即变成
    ``user(checkpoint) → tool(...)``，违反 OpenAI/DeepSeek 的 tool 协议，
    provider 返回 400「Messages with role 'tool' must be a response to a
    preceding message with 'tool_calls'」，对话直接中断。

    校正方向固定为**前移**（把整个工具轮划入待摘要区）：保证返回值单调不增，
    与 :meth:`ContextCompactionService._generate_summary` 的防重复判定
    （``previous.message_count >= len(head)`` 则跳过）同向，不会出现
    「校正后判定重复 → 永不压缩」的死循环。

    Args:
        messages: 会话原始历史。
        cutoff: 期望的切点（先 clamp 到 ``[0, len(messages)]``）。

    Returns:
        安全切点: 若 ``messages[cutoff]`` 不是 ``tool`` 则原样返回；
        若是 ``tool`` 则返回其所属工具轮的 ``assistant(tool_calls)`` 下标；
        找不到父级（脏数据）时原样返回，由 ``_build_messages`` 的孤儿过滤兜底。
    """
    cutoff = max(0, min(cutoff, len(messages)))
    if cutoff <= 0 or cutoff >= len(messages):
        return cutoff
    if messages[cutoff].get("role") != "tool":
        return cutoff
    for index in range(cutoff - 1, -1, -1):
        message = messages[index]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            return index
    return cutoff


def project_messages(
    messages: list[dict[str, Any]],
    context_compaction: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """压缩投影：有压缩点时输出 [checkpoint] + 压缩点之后的消息。

    切点经 :func:`safe_cutoff` 校正（ADR-054 决策 A）：投影序列的首条历史消息
    **永不为 ``tool``**。压缩点落库时已校正一次，此处再校正一次作双保险 ——
    存量脏压缩点（P1 之前写入、cutoff 落在 ``tool`` 上）在新代码下同样不会
    产生协议非法投影。
    """
    if not context_compaction or not context_compaction.get("summary_text"):
        return list(messages)
    cutoff = max(0, min(int(context_compaction.get("message_count") or 0), len(messages)))
    cutoff = safe_cutoff(messages, cutoff)
    projected: list[dict[str, Any]] = [build_checkpoint_message(context_compaction["summary_text"])]
    projected.extend(messages[cutoff:])
    return projected


class ContextCompactionService:
    """会话上下文压缩服务（原文保留式）。"""

    async def compact_if_needed(
        self,
        session: AsyncSession,
        building_session: TaskBuildingSession,
        chat_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        client: Any,
        force: bool = False,
        system_text: str | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        """预压/兜底压缩入口。

        Args:
            session: 异步数据库会话。
            building_session: 任务构建会话行（读 context_compaction、写压缩点）。
            chat_messages: 本轮完整对话历史（含工具轨迹）。
            tools: OpenAI tools 定义（参与体积估算）。
            client: DeepSeekClient（摘要生成 + context_window 读取）。
            force: 强制压缩（overflow 兜底场景——估算可能低估而 provider
                实际溢出，跳过预压判定直接执行）。
            system_text: system prompt 内容（ADR-041 修订：体积估算必须计入，
                否则技能清单/环境块的大头被漏算导致判定失效）。

        Returns:
            (是否执行了压缩, 最新压缩点)；未压缩返回 (False, 原压缩点)。
        """
        context_window = int(getattr(client, "_config", None).context_window) if client else 65536
        current = building_session.context_compaction
        if not force and not should_pre_compact(chat_messages, tools, context_window, system_text):
            return False, current
        summary_text, cutoff = await self._generate_summary(
            session,
            building_session,
            chat_messages,
            client,
            context_window,
            system_text=system_text,
            tools=tools,
        )
        if summary_text is None:
            return False, current
        compaction = {
            "summary_text": summary_text,
            "message_count": cutoff,
            # 切点角色（ADR-054）：落库值已过 safe_cutoff，此处记录以便事后审计
            # 「压缩切点是否曾落在工具轮上」——恒不为 "tool"
            "cutoff_role": (
                chat_messages[cutoff].get("role") if cutoff < len(chat_messages) else None
            ),
            "created_at": datetime.now(UTC).isoformat(),
        }
        building_session.context_compaction = compaction
        await session.commit()
        logger.info(
            "context_compaction_done",
            extra={
                "event": "context_compaction_done",
                "session_id": str(building_session.id),
                "message_count": cutoff,
                "cutoff_role": compaction["cutoff_role"],
                "summary_tokens": estimate_tokens(summary_text),
                "context_window": context_window,
            },
        )
        return True, compaction

    async def _generate_summary(
        self,
        session: AsyncSession,
        building_session: TaskBuildingSession,
        chat_messages: list[dict[str, Any]],
        client: Any,
        context_window: int = 65536,
        system_text: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str | None, int]:
        """生成五段锚定摘要；失败返回 (None, 0) 不中断对话。

        切点经 :func:`safe_cutoff` 校正（ADR-054 决策 A），返回值第二项为
        **校正后**的切点，供落库使用。

        摘要调用按三级降级（ADR-054 决策 B），目的是让辅助调用尽量成为上一次
        真实请求的真前缀以复用 provider KV 缓存，同时保证「压缩永远可用」：

        1. 真前缀 + tools：真实 system + 本次 tools + 被压缩区消息逐字 + 尾部指令；
        2. 真前缀（不带 tools）：兼容不支持 tools 的非流式调用；
        3. 原独立调用形态（合成 system + 单条 user 拼装文本）：P1 之前的行为。
        """
        # recent 保留量随窗口自适应（小窗口测试场景保留 8000 会导致 head 恒空）
        head, recent = split_for_compaction(
            chat_messages, recent_keep_tokens=recent_keep_tokens_for(context_window)
        )
        if not head:
            return None, 0
        # 安全切点（ADR-054 决策 A）：切点前移到工具轮之外，避免投影以孤儿 tool 开头
        cutoff = safe_cutoff(chat_messages, len(head))
        if cutoff <= 0:
            # 前移后无可摘要内容（整个历史都在一轮工具内）：本轮不压缩
            logger.info(
                "context_compaction_skipped_unsafe_cutoff",
                extra={
                    "event": "context_compaction_skipped_unsafe_cutoff",
                    "head_messages": len(head),
                },
            )
            return None, 0
        head = chat_messages[:cutoff]
        previous = building_session.context_compaction
        # 防重复摘要：上次压缩点已覆盖本次 head 终点（新消息未推进切分点）
        # 时跳过，避免每轮对相同内容重复调用摘要 LLM
        if previous and int(previous.get("message_count") or 0) >= len(head):
            return None, 0
        instruction = build_summary_instruction(previous.get("summary_text") if previous else None)
        summary_text = await self._summarize_with_fallback(
            client, system_text, head, instruction, tools
        )
        if not summary_text:
            return None, 0
        return summary_text.strip(), cutoff

    async def _summarize_with_fallback(
        self,
        client: Any,
        system_text: str | None,
        head: list[dict[str, Any]],
        instruction: str,
        tools: list[dict[str, Any]] | None,
    ) -> str | None:
        """摘要 LLM 调用的三级降级（ADR-054 决策 B）。

        Returns:
            摘要正文；三档全部失败返回 ``None``（调用方跳过本轮压缩）。
        """
        attempts: list[tuple[str, list[dict[str, str]], list[dict[str, Any]] | None]] = []
        if system_text:
            # 真前缀：真实 system + 被压缩区消息逐字重放 + 尾部指令
            prefix_messages: list[dict[str, str]] = [
                {"role": "system", "content": system_text},
                *head,
                {"role": "user", "content": instruction},
            ]
            if tools:
                attempts.append(("prefix_with_tools", prefix_messages, tools))
            attempts.append(("prefix", prefix_messages, None))
        # 兜底：P1 之前的独立调用形态
        attempts.append(
            (
                "standalone",
                [
                    {"role": "system", "content": "你是对话检查点摘要器，只输出摘要正文。"},
                    {
                        "role": "user",
                        "content": build_summary_prompt(head, [], instruction),
                    },
                ],
                None,
            )
        )
        for tier, messages, tier_tools in attempts:
            try:
                text = await client.chat_completion_messages(
                    messages,
                    tools=tier_tools,
                    max_tokens=SUMMARY_MAX_OUTPUT_TOKENS,
                    # ADR-056：与主请求保持同一思考开关，避免前缀对齐之外的
                    # 参数语义分叉（前者不影响 provider 缓存，但保持一致性更好排查）
                    thinking=get_settings().orchestrator_thinking_enabled,
                )
            except TypeError:
                # 兼容不支持 tools 关键字的历史/替身客户端：退化为不带 tools 调用
                if tier_tools is None:
                    raise
                logger.info(
                    "context_compaction_summary_prefix_tools_unsupported",
                    extra={"event": "context_compaction_summary_prefix_tools_unsupported"},
                )
                continue
            except Exception:
                logger.warning(
                    "context_compaction_summary_tier_failed",
                    extra={"event": "context_compaction_summary_tier_failed", "tier": tier},
                    exc_info=True,
                )
                continue
            if text and text.strip():
                logger.info(
                    "context_compaction_summary_done",
                    extra={
                        "event": "context_compaction_summary_done",
                        "tier": tier,
                        "with_tools": bool(tier_tools),
                        "replayed_messages": len(messages),
                    },
                )
                return text
        logger.warning(
            "context_compaction_summary_failed",
            extra={"event": "context_compaction_summary_failed"},
        )
        return None
