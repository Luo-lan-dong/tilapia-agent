"""上下文压缩服务单元测试（ADR-041 / ADR-054）。

验证 token 估算与密度校准、预压触发、切分、摘要 prompt 组装、压缩投影与原文保留、
安全切点与摘要热前缀复用三级降级；不依赖真实数据库（会话行用 SimpleNamespace 模拟）。
"""

from types import SimpleNamespace

import pytest

from app.services.context_compaction_service import (
    CALIBRATION_MAX_CHARS_PER_TOKEN,
    CALIBRATION_MIN_CHARS_PER_TOKEN,
    CALIBRATION_MIN_PROMPT_TOKENS,
    CHARS_PER_TOKEN,
    ContextCompactionService,
    build_checkpoint_message,
    build_summary_instruction,
    build_summary_prompt,
    calibrate_chars_per_token,
    compaction_buffer_tokens,
    compaction_threshold,
    estimate_chars_per_token,
    estimate_messages_tokens,
    estimate_tokens,
    is_context_overflow_error,
    measure_payload_chars,
    project_messages,
    recent_keep_tokens_for,
    safe_cutoff,
    should_compact_now,
    should_pre_compact,
    split_for_compaction,
)


class _FakeSummaryClient:
    """模拟 DeepSeekClient：摘要生成返回固定文本。

    记录每次调用的 messages（``calls``，兼容既有断言）与随调用传入的
    ``tools``（``tool_args``，供 ADR-054 前缀对齐断言）。
    """

    def __init__(self, summary: str = "## Objective\n测试摘要", fail: bool = False) -> None:
        self._config = SimpleNamespace(context_window=65536)
        self._summary = summary
        self._fail = fail
        self.calls: list[list[dict]] = []
        self.tool_args: list[list[dict] | None] = []

    async def chat_completion_messages(
        self, messages: list[dict], tools: list[dict] | None = None, **_: object
    ) -> str:
        self.calls.append(messages)
        self.tool_args.append(tools)
        if self._fail:
            raise RuntimeError("summary provider down")
        return self._summary


class _TieredSummaryClient:
    """按调用序号控制成功/失败的摘要客户端（ADR-054 三级降级测试用）。

    ``outcomes`` 为每档的结果：``"ok"`` 成功、``"error"`` 抛异常、
    ``"empty"`` 返回空串、``"nokw"`` 抛 TypeError（模拟不支持 tools 关键字）。
    超出列表长度的调用一律成功。
    """

    def __init__(self, outcomes: list[str], summary: str = "## Objective\n分级摘要") -> None:
        self._config = SimpleNamespace(context_window=65536)
        self._outcomes = list(outcomes)
        self._summary = summary
        self.calls: list[list[dict]] = []
        self.tool_args: list[list[dict] | None] = []

    async def chat_completion_messages(
        self, messages: list[dict], tools: list[dict] | None = None, **_: object
    ) -> str:
        index = len(self.calls)
        self.calls.append(messages)
        self.tool_args.append(tools)
        outcome = self._outcomes[index] if index < len(self._outcomes) else "ok"
        if outcome == "nokw":
            raise TypeError("unexpected keyword argument 'tools'")
        if outcome == "error":
            raise RuntimeError("summary tier down")
        if outcome == "empty":
            return "   "
        return self._summary


def _msg(role: str, content: str, **extra: object) -> dict:
    return {"role": role, "content": content, **extra}


def test_estimate_tokens_chars_per_2() -> None:
    """UT-18（ADR-041 修订 2026-09-27）：密度参数化 —— 缺省种子 + 显式注入。

    原 UT-1 硬断言「chars/2」；修订后常数降级为冷启动种子，密度可注入，
    故同时断言两者：显式传参精确，缺省等价于种子常数（向后兼容）。
    """
    assert estimate_tokens("abcd" * 10, chars_per_token=2) == 20
    assert estimate_tokens("abcd" * 10, chars_per_token=4) == 10
    assert estimate_tokens("abcd" * 10) == 20
    assert CHARS_PER_TOKEN == 2


def test_estimate_tokens_density_ratio_is_linear() -> None:
    """UT-22：密度注入对估算的影响可预期（2.5 与 2 的估算比为 2.5/2）。"""
    text = "x" * 10_000
    at_seed = estimate_tokens(text, chars_per_token=2)
    at_calibrated = estimate_tokens(text, chars_per_token=2.5)
    assert at_seed == 5000
    assert at_calibrated == 4000
    assert at_seed / at_calibrated == 2.5 / 2


def test_measure_payload_chars_same_scale_as_estimate() -> None:
    """校准口径一致性：measure_payload_chars 与估算使用同一字符口径。"""
    messages = [_msg("user", "x" * 300)]
    tools = [{"type": "function", "function": {"name": "t"}}]
    chars = measure_payload_chars(messages, tools, "SYS")
    assert chars > 300
    assert estimate_tokens_from_chars(chars, 2) == estimate_messages_tokens(
        messages, tools, "SYS", 2
    )
    assert estimate_tokens_from_chars(chars, 4) == estimate_messages_tokens(
        messages, tools, "SYS", 4
    )


def estimate_tokens_from_chars(chars: int, chars_per_token: float) -> int:
    """测试内联换算：与 estimate_tokens 相同的四舍五入规则。"""
    return round(chars / chars_per_token)


def test_calibrate_chars_per_token_ema_from_seed() -> None:
    """UT-19：首次校准以种子常数起算并做 EMA（α=0.3）。"""
    # 观测密度 5000/2000 = 2.5；2 + 0.3*(2.5-2) = 2.15
    assert calibrate_chars_per_token(5000, 2000, None) == pytest.approx(2.15)
    # 已有校准值 3.0 时：3.0 + 0.3*(2.5-3.0) = 2.85
    assert calibrate_chars_per_token(5000, 2000, 3.0) == pytest.approx(2.85)


def test_calibrate_chars_per_token_clamps() -> None:
    """UT-20：观测密度越界时夹取到 [1.5, 6.0]（防单次异常 usage 长期拉偏）。"""
    # 观测 0.5 → 夹到下限 1.5；previous=2 → 2 + 0.3*(1.5-2) = 1.85
    assert calibrate_chars_per_token(1000, 2000, 2.0) == pytest.approx(1.85)
    # 观测 20.0 → 夹到上限 6.0；previous=2 → 2 + 0.3*(6-2) = 3.2
    assert calibrate_chars_per_token(40_000, 2000, 2.0) == pytest.approx(3.2)
    assert CALIBRATION_MIN_CHARS_PER_TOKEN == 1.5
    assert CALIBRATION_MAX_CHARS_PER_TOKEN == 6.0


def test_calibrate_chars_per_token_noise_guard() -> None:
    """UT-21：样本低于噪声门槛时保持既有值（不校准）。"""
    assert CALIBRATION_MIN_PROMPT_TOKENS == 2000
    for real_tokens in (None, 0, 1999):
        assert calibrate_chars_per_token(5000, real_tokens, 2.7) == 2.7
        assert calibrate_chars_per_token(5000, real_tokens, None) is None
    # 恰好达门槛即参与校准
    assert calibrate_chars_per_token(5000, 2000, 2.0) == pytest.approx(2.15)
    # 无字符（空请求体）不校准
    assert calibrate_chars_per_token(0, 5000, 2.4) == 2.4


def test_estimate_chars_per_token_reads_compaction() -> None:
    """校准值读取：有则取（并夹取），无/非法则回退种子常数。"""
    assert estimate_chars_per_token(None) == float(CHARS_PER_TOKEN)
    assert estimate_chars_per_token({}) == float(CHARS_PER_TOKEN)
    assert estimate_chars_per_token({"est_chars_per_token": 2.55}) == pytest.approx(2.55)
    # 越界值被夹取
    assert estimate_chars_per_token({"est_chars_per_token": 0.1}) == 1.5
    assert estimate_chars_per_token({"est_chars_per_token": 99}) == 6.0
    # 非法类型回退常数（bool 是 int 子类，必须显式排除）
    assert estimate_chars_per_token({"est_chars_per_token": True}) == float(CHARS_PER_TOKEN)
    assert estimate_chars_per_token({"est_chars_per_token": "x"}) == float(CHARS_PER_TOKEN)


def test_should_pre_compact_uses_calibrated_density() -> None:
    """校准密度参与触发判定：更小的密度（更接近真实）需要更多字符才越线。"""
    window = 65536
    line = compaction_threshold(window)  # 37344
    # 字符数刚好使 density=1 时越线、density=2 时不越线
    messages = [_msg("user", "x" * (line * 2 + 200))]
    assert should_pre_compact(messages, None, window, None, 2)
    assert not should_pre_compact(messages, None, window, None, 4)


def test_should_compact_now_requires_new_head() -> None:
    """UT-23：压缩点已覆盖本次 head 时不判定为「确将推进」。"""
    window = 65536
    messages = [_msg("user", "a" * 4000) for _ in range(20)]
    head, _ = split_for_compaction(messages, recent_keep_tokens=recent_keep_tokens_for(window))
    assert head, "用例前提：需要可切出 head"
    covered = {"summary_text": "旧摘要", "message_count": len(head)}
    assert not should_compact_now(messages, None, window, None, covered)
    # 压缩点覆盖更多（更早的历史）同样不推进
    assert not should_compact_now(
        messages, None, window, None, {"summary_text": "旧", "message_count": len(head) + 5}
    )


def test_should_compact_now_head_from_raw_history() -> None:
    """UT-24b：head 必须按**原始历史**切分（与摘要端防重复同源）。

    回归对象：压缩点已落库时，请求体是「checkpoint + 压缩点之后的消息」，按它
    切分 head 会与摘要端（读原始历史）不一致，导致事件与实际压缩行为错配。
    本用例的压力口径（pressure_messages）构造为确实越线，切分口径用原始历史。
    """
    window = 65536
    threshold = compaction_threshold(window)
    messages = [_msg("user", "a" * 4000) for _ in range(20)]
    head, _ = split_for_compaction(messages, recent_keep_tokens=recent_keep_tokens_for(window))
    assert head
    # 投影后的请求：大 checkpoint（越线来源）+ 尾部消息，其 head 比原始历史短
    pressure = [build_checkpoint_message("摘要" * (threshold * 2)), *messages[-3:]]
    assert should_pre_compact(pressure, None, window), "用例前提：压力口径需越线"
    pressure_head, _ = split_for_compaction(
        pressure, recent_keep_tokens=recent_keep_tokens_for(window)
    )
    assert len(pressure_head) < len(head), "用例前提：投影后的 head 更短"
    # 压缩点落后 1 条：按原始历史判定可推进
    behind = {"summary_text": "旧", "message_count": len(head) - 1}
    assert should_compact_now(messages, None, window, None, behind, 2, pressure_messages=pressure)
    # 压缩点已覆盖原始 head：不可推进（即使压力仍越线）
    covered = {"summary_text": "旧", "message_count": len(head)}
    assert not should_compact_now(
        messages, None, window, None, covered, 2, pressure_messages=pressure
    )


def test_should_compact_now_requires_pressure() -> None:
    """UT-24c：压力口径未越线则不推进（判定读的是实际请求，不再读原始全量历史）。"""
    window = 65536
    messages = [_msg("user", "a" * 4000) for _ in range(20)]
    head, _ = split_for_compaction(messages, recent_keep_tokens=recent_keep_tokens_for(window))
    # 原始历史很大，但实际请求（投影后）很小 → 不推进
    tiny_pressure = [build_checkpoint_message("摘要"), _msg("user", "hi")]
    assert should_pre_compact(messages, None, window), "用例前提：原始历史越线"
    assert not should_compact_now(
        messages,
        None,
        window,
        None,
        {"message_count": len(head) - 1},
        2,
        pressure_messages=tiny_pressure,
    )


def test_should_compact_now_true_when_head_grew() -> None:
    """UT-24：越线且 head 相对既有压缩点有新增 → 判定推进；未越线 → False。"""
    window = 65536
    messages = [_msg("user", "a" * 4000) for _ in range(20)]
    head, _ = split_for_compaction(messages, recent_keep_tokens=recent_keep_tokens_for(window))
    assert should_compact_now(messages, None, window, None, None)
    assert should_compact_now(
        messages, None, window, None, {"summary_text": "旧", "message_count": len(head) - 1}
    )
    # 未越线：小历史
    assert not should_compact_now([_msg("user", "hi")], None, window, None, None)
    # head 为空（历史太短无法切分）→ 不推进
    assert not should_compact_now(
        [_msg("user", "hi")], None, 400, None, {"summary_text": "旧", "message_count": 0}
    )


def test_estimate_messages_tokens_includes_tools() -> None:
    """tools 定义体积计入总占用估算。"""
    messages = [_msg("user", "x" * 400)]
    base = estimate_messages_tokens(messages, None)
    with_tools = estimate_messages_tokens(messages, [{"type": "function", "x": "y" * 400}])
    assert with_tools > base


def test_compaction_threshold_reserves_output_and_buffer() -> None:
    """触发线 = 窗口 − 8192 − 缓冲（小窗口下缓冲取下限 20000）。"""
    assert compaction_threshold(65536) == 65536 - 8192 - 20000


def test_compaction_buffer_scales_with_window() -> None:
    """UT-54~56（ADR-056）：缓冲 = max(20000, 窗口×5%)，封顶 100000。"""
    assert compaction_buffer_tokens(65536) == 20000  # 比例值更小 → 取下限
    assert compaction_buffer_tokens(1000000) == 50000  # 5%
    assert compaction_buffer_tokens(4000000) == 100000  # 封顶
    assert compaction_buffer_tokens(100000) == 20000  # 边界：比例=5000 → 取下限


def test_threshold_with_1m_window_does_not_compress_at_low_usage() -> None:
    """UT-57（ADR-056 守卫）：1M 窗口下真实低占用**不**触发压缩。

    回归对象：窗口口径错误（65536）时，3 万 token 历史（真实占用 ~4.6%）
    就会越过触发线 37344 而触发压缩；校正为 1M 后触发线约 94% 窗口，不应触发。
    """
    window = 1000000
    assert compaction_threshold(window) == 1000000 - 8192 - 50000
    assert compaction_threshold(65536) == 37344

    # 用例前提：估算量落在「旧 64K 触发线之上、1M 触发线之下」的区间，
    # 才能同时体现「旧口径会误触发」与「新口径不触发」
    messages = [_msg("user", "a" * 3000) for _ in range(36)]
    estimated = estimate_messages_tokens(messages, None)
    assert compaction_threshold(65536) < estimated < compaction_threshold(window), (
        f"用例前提不成立：估算 {estimated} 未落在 "
        f"({compaction_threshold(65536)}, {compaction_threshold(window)}) 区间"
    )
    # 1M 窗口：不触发；旧 64K 口径：触发（对照，说明口径修复的必要性）
    assert not should_pre_compact(messages, None, window)
    assert should_pre_compact(messages, None, 65536)

    # 足够大时仍会触发（1M 窗口下需要 ~94 万 token）
    big = [_msg("user", "a" * 100000) for _ in range(20)]
    assert should_pre_compact(big, None, window)


def test_should_pre_compact_boundary() -> None:
    """UT-2：超线触发 / 边界内不触发。"""
    window = 65536
    small = [_msg("user", "hi")]
    assert not should_pre_compact(small, None, window)
    huge_text = "x" * (compaction_threshold(window) * 4 + 100)
    assert should_pre_compact([_msg("user", huge_text)], None, window)


def test_is_context_overflow_error_keywords() -> None:
    """UT-13 前置：仅 400 且含长度关键词判为 overflow。"""
    assert is_context_overflow_error(400, "This model's maximum context length is exceeded")
    assert is_context_overflow_error(400, "input length exceeds token limit")
    assert not is_context_overflow_error(400, "invalid api key")
    assert not is_context_overflow_error(429, "rate limited")


def test_split_for_compaction_keeps_recent_tail() -> None:
    """UT-3：尾部 recent 保留、其余进 head；短历史 head 为空。"""
    short = [_msg("user", "hi"), _msg("assistant", "hello")]
    head, recent = split_for_compaction(short, recent_keep_tokens=100)
    assert head == []
    assert len(recent) == 2

    long_messages = [_msg("user", "a" * 4000) for _ in range(20)]  # 每条约 1000 token
    head, recent = split_for_compaction(long_messages, recent_keep_tokens=5000)
    assert len(head) + len(recent) == 20
    assert head and recent
    assert head[-1] is not recent[0]
    assert long_messages[len(head)] is recent[0]


def test_split_for_compaction_serializes_tool_calls() -> None:
    """UT-4：tool_calls 转为「调用技能」标注；tool 消息带 call_id 前缀。"""
    prompt = build_summary_prompt(
        [
            _msg(
                "assistant",
                "",
                tool_calls=[
                    {"id": "c1", "function": {"name": "find_idle_nodes", "arguments": "{}"}}
                ],
            ),
            _msg("tool", "节点 code 空闲", tool_call_id="c1"),
            _msg("user", "x" * 5000),
        ],
        [],
        None,
    )
    assert "[调用技能: find_idle_nodes]" in prompt
    assert "c1 → 节点 code 空闲" in prompt


def test_build_summary_prompt_anchors_previous_summary() -> None:
    """UT-5：五段模板 + 旧摘要锚定注入。"""
    prompt = build_summary_prompt([_msg("user", "hi")], [], "旧摘要内容")
    assert "## Objective" in prompt and "## Relevant Tasks & Nodes" in prompt
    assert "Previous checkpoint summary" in prompt
    assert "旧摘要内容" in prompt


@pytest.mark.asyncio
async def test_compact_writes_compaction_and_keeps_original() -> None:
    """UT-6：压缩落库 context_compaction；messages 列原文不变。"""
    building_session = SimpleNamespace(
        id="s-1",
        context_compaction=None,
        messages=[_msg("user", "原始消息，请勿修改")],
    )
    long_messages = [_msg("user", "a" * 4000) for _ in range(20)]
    client = _FakeSummaryClient(summary="## Objective\nsum")
    service = ContextCompactionService()

    class _FakeDbSession:
        async def commit(self) -> None:
            return None

    did, compaction = await service.compact_if_needed(
        _FakeDbSession(),  # type: ignore[arg-type]
        building_session,  # type: ignore[arg-type]
        long_messages,
        None,
        client,
        force=True,
    )
    assert did is True
    assert compaction["summary_text"] == "## Objective\nsum"
    assert 0 < compaction["message_count"] < 20
    # messages 列原文永不修改
    assert building_session.messages == [_msg("user", "原始消息，请勿修改")]
    assert building_session.context_compaction["message_count"] == compaction["message_count"]


@pytest.mark.asyncio
async def test_compact_summary_failure_skips_gracefully() -> None:
    """UT-9：摘要生成失败不抛出，返回 (False, 原压缩点)。"""
    building_session = SimpleNamespace(id="s-1", context_compaction={"summary_text": "old"})
    client = _FakeSummaryClient(fail=True)
    service = ContextCompactionService()

    class _FakeDbSession:
        async def commit(self) -> None:
            return None

    did, compaction = await service.compact_if_needed(
        _FakeDbSession(),  # type: ignore[arg-type]
        building_session,  # type: ignore[arg-type]
        [_msg("user", "a" * 4000) for _ in range(10)],
        None,
        client,
        force=True,
    )
    assert did is False
    assert compaction == {"summary_text": "old"}


def test_project_messages_with_compaction_point() -> None:
    """UT-7：有压缩点时输出 [checkpoint] + 之后的消息。"""
    messages = [_msg("user", f"m{i}") for i in range(5)]
    compaction = {"summary_text": "摘要全文", "message_count": 3}

    projected = project_messages(messages, compaction)

    assert len(projected) == 3  # checkpoint + 2 条
    checkpoint = projected[0]
    assert checkpoint["role"] == "user"
    assert "<conversation-checkpoint>" in checkpoint["content"]
    assert "摘要全文" in checkpoint["content"]
    assert "history_recall" in checkpoint["content"]
    assert projected[1]["content"] == "m3"
    assert projected[2]["content"] == "m4"


def test_project_messages_without_compaction_unchanged() -> None:
    """UT-8：无压缩点时投影与原列表一致（回归）。"""
    messages = [_msg("user", "hi")]
    assert project_messages(messages, None) == messages
    assert project_messages(messages, {"summary_text": None, "message_count": 3}) == messages


def test_checkpoint_message_content() -> None:
    """checkpoint 消息为 user 角色，含摘要与回查提示。"""
    checkpoint = build_checkpoint_message("SUM")
    assert checkpoint["role"] == "user"
    assert "SUM" in checkpoint["content"]
    assert "history_recall" in checkpoint["content"]


def test_should_pre_compact_counts_system_text() -> None:
    """修复回归（2026-09-22）：预压估算必须计入 system prompt。"""
    window = 65536
    small_messages = [_msg("user", "hi")]
    # 仅 messages+tools：不触发（触发线 = 65536-8192-20000 = 37344）
    assert not should_pre_compact(small_messages, None, window)
    # 计入 system（16 万字符 ≈ 4 万 token）：触发
    system_text = "x" * 160_000
    assert should_pre_compact(small_messages, None, window, system_text=system_text)


def test_recent_keep_tokens_adapts_to_small_window() -> None:
    """修复回归（2026-09-22）：recent 保留量随窗口缩小，小窗口下 head 可切出。"""
    assert recent_keep_tokens_for(65536) == 8000
    assert recent_keep_tokens_for(1200) == 150  # 1200//8，下限 128
    assert recent_keep_tokens_for(64) == 128  # 下限兜底

    # 小窗口（keep=150）下：每条 250 字符 ≈ 125 token → 尾部 1 条留 recent，
    # 其余 5 条进 head（此前固定 8000 导致全部进 recent、head 恒空）
    long_messages = [_msg("user", "a" * 250) for _ in range(6)]
    head, recent = split_for_compaction(
        long_messages, recent_keep_tokens=recent_keep_tokens_for(1200)
    )
    assert len(head) == 5
    assert len(recent) == 1


@pytest.mark.asyncio
async def test_compact_skips_when_already_covered() -> None:
    """修复回归（2026-09-22）：压缩点已覆盖本次 head 时跳过，不重复调用摘要 LLM。"""
    compaction = {"summary_text": "旧摘要", "message_count": 5}
    building_session = SimpleNamespace(id="s-1", context_compaction=compaction)
    client = _FakeSummaryClient()
    service = ContextCompactionService()

    class _FakeDbSession:
        async def commit(self) -> None:
            return None

    # 6 条消息、小窗口 keep=150 → head 约 5 条；压缩点 5 已覆盖 → 跳过
    messages = [_msg("user", "a" * 4000) for _ in range(6)]
    did, current = await service.compact_if_needed(
        _FakeDbSession(),  # type: ignore[arg-type]
        building_session,  # type: ignore[arg-type]
        messages,
        None,
        client,
        force=True,
        system_text=None,
    )
    assert did is False
    assert current is compaction
    # 摘要 LLM 未被调用
    assert client.calls == []


# ---------------------------------------------------------------------------
# ADR-054 决策 A：安全切点（治 provider 400 中断）
# ---------------------------------------------------------------------------

_TOOL_TURN = [
    _msg("user", "帮我看下节点"),
    _msg(
        "assistant",
        "",
        tool_calls=[{"id": "c1", "function": {"name": "find_idle_nodes", "arguments": "{}"}}],
    ),
    _msg("tool", "节点 code 空闲", tool_call_id="c1"),
    _msg("tool", "节点 video 忙碌", tool_call_id="c1"),
    _msg("user", "谢谢"),
]


def test_safe_cutoff_moves_before_tool_turn() -> None:
    """UT-28：切点落在 tool 上 → 前移到该工具轮的 assistant(tool_calls) 之前。"""
    # messages[2] 是 tool，其父级在 1 → 返回 1
    assert safe_cutoff(_TOOL_TURN, 2) == 1


def test_safe_cutoff_moves_for_any_tool_in_turn() -> None:
    """UT-29：同一工具轮的后续 tool 消息同样前移到轮首。"""
    assert safe_cutoff(_TOOL_TURN, 3) == 1


def test_safe_cutoff_passthrough_when_not_tool() -> None:
    """UT-30：切点不在 tool 上时原样返回。"""
    assert safe_cutoff(_TOOL_TURN, 0) == 0
    assert safe_cutoff(_TOOL_TURN, 1) == 1
    assert safe_cutoff(_TOOL_TURN, 4) == 4
    assert safe_cutoff(_TOOL_TURN, 5) == 5


def test_safe_cutoff_is_monotonic_non_increasing() -> None:
    """UT-31：方向不变量 safe_cutoff(m, c) <= c（防「校正后判定重复→永不压缩」）。"""
    sequences = [
        _TOOL_TURN,
        [_msg("tool", "孤儿", tool_call_id="x"), _msg("user", "hi")],
        [_msg("assistant", "纯文本"), _msg("tool", "t", tool_call_id="c")],
        [_msg("user", "a"), _msg("assistant", "", tool_calls=[{"id": "c"}])],
    ]
    for messages in sequences:
        for cutoff in range(0, len(messages) + 3):
            assert safe_cutoff(messages, cutoff) <= min(cutoff, len(messages))


def test_safe_cutoff_clamps_and_tolerates_dirty_data() -> None:
    """UT-32：越界先 clamp；找不到父级（脏数据）时原样返回，不抛错。"""
    assert safe_cutoff(_TOOL_TURN, -5) == 0
    assert safe_cutoff(_TOOL_TURN, 99) == len(_TOOL_TURN)
    orphan = [_msg("tool", "无父级", tool_call_id="x"), _msg("user", "hi")]
    assert safe_cutoff(orphan, 0) == 0
    assert safe_cutoff([_msg("user", "x"), _msg("tool", "无父级", tool_call_id="y")], 1) == 1


def test_project_messages_never_starts_with_tool() -> None:
    """UT-34：任意压缩点下投影首条历史消息不为 tool。"""
    compaction = {"summary_text": "摘要", "message_count": 2}
    projected = project_messages(_TOOL_TURN, compaction)
    assert projected[0]["role"] == "user"
    assert "<conversation-checkpoint>" in projected[0]["content"]
    assert projected[1]["role"] != "tool"
    # 前移到 1：投影携带 assistant(tool_calls) + 其 tool 响应 + 后续
    assert projected[1]["role"] == "assistant"
    assert projected[1]["tool_calls"]


def test_project_messages_fixes_legacy_dirty_cutoff() -> None:
    """UT-35：存量脏压缩点（cutoff 落 tool）在新代码下投影仍合法。"""
    legacy = {"summary_text": "旧摘要", "message_count": 3}  # 落在第二个 tool 上
    projected = project_messages(_TOOL_TURN, legacy)
    assert projected[1]["role"] != "tool"


@pytest.mark.asyncio
async def test_compact_skips_when_safe_cutoff_is_zero() -> None:
    """UT-33：安全切点前移后无可摘要内容 → 不压缩、不调用摘要 LLM。"""

    class _FakeDbSession:
        async def commit(self) -> None:
            return None

    building_session = SimpleNamespace(id="s-1", context_compaction=None)
    client = _FakeSummaryClient()
    service = ContextCompactionService()
    # 整个历史就是一轮工具：任何切点都会前移到 0
    messages = [
        _msg("user", "a" * 4000),
        _msg("assistant", "", tool_calls=[{"id": "c1"}]),
        _msg("tool", "b" * 4000, tool_call_id="c1"),
        _msg("tool", "c" * 4000, tool_call_id="c1"),
    ]
    did, compaction = await service.compact_if_needed(
        _FakeDbSession(),  # type: ignore[arg-type]
        building_session,  # type: ignore[arg-type]
        messages,
        None,
        client,
        force=True,
        system_text="SYS",
    )
    assert did is False
    assert compaction is None
    assert client.calls == []


@pytest.mark.asyncio
async def test_compact_persists_safe_cutoff_and_cutoff_role() -> None:
    """UT-40：落库 message_count 为安全切点，并记录 cutoff_role（恒不为 tool）。"""

    class _FakeDbSession:
        async def commit(self) -> None:
            return None

    building_session = SimpleNamespace(id="s-1", context_compaction=None)
    # 每条 4000 字符 ≈ 2000 token；keep=8000 → head ≈ 16 条，落在 tool 上
    messages: list[dict] = []
    for index in range(20):
        if index == 15:
            messages.append(_msg("assistant", "", tool_calls=[{"id": f"c{index}"}]))
            messages.append(_msg("tool", "x" * 4000, tool_call_id=f"c{index}"))
            continue
        messages.append(_msg("user", f"m{index}，" + "a" * 4000))
    client = _FakeSummaryClient()
    service = ContextCompactionService()
    did, compaction = await service.compact_if_needed(
        _FakeDbSession(),  # type: ignore[arg-type]
        building_session,  # type: ignore[arg-type]
        messages,
        None,
        client,
        force=True,
        system_text="SYS",
    )
    assert did is True
    assert compaction["cutoff_role"] != "tool"
    assert compaction["message_count"] == safe_cutoff(messages, compaction["message_count"])
    assert building_session.context_compaction["cutoff_role"] == compaction["cutoff_role"]


# ---------------------------------------------------------------------------
# ADR-054 决策 B：摘要热前缀复用 + 三级降级
# ---------------------------------------------------------------------------


def test_build_summary_instruction_content() -> None:
    """UT-39：尾部指令含五段模板与规则；有旧摘要时含锚定段。"""
    plain = build_summary_instruction()
    assert "## Objective" in plain
    assert "## Relevant Tasks & Nodes" in plain
    assert "不要调用任何工具" in plain
    assert "已有检查点摘要" not in plain

    anchored = build_summary_instruction("旧摘要正文")
    assert "已有检查点摘要" in anchored
    assert "旧摘要正文" in anchored


async def _compact_with(messages: list[dict], client: object, system_text: str | None = "SYS-TEXT"):
    class _FakeDbSession:
        async def commit(self) -> None:
            return None

    service = ContextCompactionService()
    return await service.compact_if_needed(
        _FakeDbSession(),  # type: ignore[arg-type]
        SimpleNamespace(id="s-1", context_compaction=None),  # type: ignore[arg-type]
        messages,
        [{"type": "function", "function": {"name": "t"}}],
        client,
        force=True,
        system_text=system_text,
    )


@pytest.mark.asyncio
async def test_summary_uses_real_prefix_with_tools() -> None:
    """UT-37 / UT-38：摘要请求 = 真实 system + 被压缩区逐字 + 尾部指令，且带 tools。"""
    messages = [_msg("user", f"m{i}，" + "a" * 4000) for i in range(20)]
    client = _FakeSummaryClient()
    did, compaction = await _compact_with(messages, client)

    assert did is True
    assert len(client.calls) == 1
    sent = client.calls[0]
    # 首条为真实 system（不是合成摘要器提示）
    assert sent[0] == {"role": "system", "content": "SYS-TEXT"}
    # 第二段为被压缩区消息逐字重放
    cutoff = compaction["message_count"]
    assert sent[1 : 1 + cutoff] == messages[:cutoff]
    # 末条为压缩指令
    assert sent[-1]["role"] == "user"
    assert "压缩引擎" in sent[-1]["content"]
    # tools 随第一优先级透传
    assert client.tool_args[0] is not None


@pytest.mark.asyncio
async def test_summary_falls_back_without_tools() -> None:
    """UT-41：带 tools 失败 → 第二优先级（同前缀但不带 tools）成功。"""
    messages = [_msg("user", f"m{i}，" + "a" * 4000) for i in range(20)]
    client = _TieredSummaryClient(["error"])
    did, _ = await _compact_with(messages, client)

    assert did is True
    assert len(client.calls) == 2
    # 两次 messages 相同（前缀一致），第二次不带 tools
    assert client.calls[0] == client.calls[1]
    assert client.tool_args[0] is not None
    assert client.tool_args[1] is None


@pytest.mark.asyncio
async def test_summary_falls_back_to_standalone() -> None:
    """UT-42：前两档失败 → 第三优先级（原独立调用形态）。"""
    messages = [_msg("user", f"m{i}，" + "a" * 4000) for i in range(20)]
    client = _TieredSummaryClient(["error", "error"])
    did, _ = await _compact_with(messages, client)

    assert did is True
    assert len(client.calls) == 3
    standalone = client.calls[2]
    assert standalone[0]["content"] == "你是对话检查点摘要器，只输出摘要正文。"
    assert client.tool_args[2] is None


@pytest.mark.asyncio
async def test_summary_all_tiers_failed_skips_compaction() -> None:
    """UT-43：三档全失败 → 不压缩、不抛出（对话不中断）。"""
    messages = [_msg("user", f"m{i}，" + "a" * 4000) for i in range(20)]
    client = _TieredSummaryClient(["error", "error", "error"])
    did, compaction = await _compact_with(messages, client)

    assert did is False
    assert compaction is None
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_summary_empty_result_treated_as_failure() -> None:
    """UT-44：空/空白返回视为失败并降级（不落库空摘要）。"""
    messages = [_msg("user", f"m{i}，" + "a" * 4000) for i in range(20)]
    client = _TieredSummaryClient(["empty", "empty"])
    did, _ = await _compact_with(messages, client)

    assert did is True
    assert len(client.calls) == 3  # 前两档空 → 第三档成功


@pytest.mark.asyncio
async def test_summary_typeerror_falls_back_to_next_tier() -> None:
    """UT-38 兼容性：客户端不支持 tools 关键字（TypeError）时降级到不带 tools。"""
    messages = [_msg("user", f"m{i}，" + "a" * 4000) for i in range(20)]
    client = _TieredSummaryClient(["nokw"])
    did, _ = await _compact_with(messages, client)

    assert did is True
    assert len(client.calls) == 2
    assert client.tool_args[0] is not None
    assert client.tool_args[1] is None


@pytest.mark.asyncio
async def test_summary_without_system_text_uses_standalone() -> None:
    """无真实 system 文本时（理论边界）直接走独立形态，不产生前缀式请求。"""
    messages = [_msg("user", f"m{i}，" + "a" * 4000) for i in range(20)]
    client = _FakeSummaryClient()
    did, _ = await _compact_with(messages, client, system_text=None)

    assert did is True
    assert len(client.calls) == 1
    assert client.calls[0][0]["content"] == "你是对话检查点摘要器，只输出摘要正文。"
    assert client.tool_args[0] is None
