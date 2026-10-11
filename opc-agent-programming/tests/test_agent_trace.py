"""ADR-063 M2：节点 Agent 会话轨迹采集测试。

覆盖三层：

1. ``EventTranslator.translate_trace``——reasoning/text 增量分段与 ended
   收尾去重、tool 生命周期（callID 关联工具名、结果摘要、退出码）；
2. ``AgentTraceCollector``——seq 单调、批量上报、脱敏、截断、背压丢弃、
   失败重试（requeue）；
3. 红线：脱敏覆盖 ``sk-`` 串与凭据赋值；上报失败不外抛。
"""

import asyncio
from typing import Any

from app.executor.agent_trace import (
    MAX_PENDING_ROWS,
    AgentTraceCollector,
    cap_text,
    redact_payload,
    redact_text,
)
from app.executor.event_translator import EventTranslator


def make_trace_event(seq: int, event_type: str, data: dict[str, Any] | None = None) -> dict:
    """构造轨迹分类用的 tilacode SSE 事件。"""
    return {"id": f"evt_{seq}", "type": event_type, "data": data or {}}


class RecordingReporter:
    """记录上报调用的测试替身（与 HubClient.report_agent_trace 同签名）。"""

    def __init__(self, fail_times: int = 0) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail_times = fail_times

    async def __call__(
        self,
        task_id: str,
        *,
        node_id: str | None = None,
        session_id: str | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("中枢不可达")
        self.calls.append(
            {"task_id": task_id, "node_id": node_id, "session_id": session_id, "rows": rows}
        )

    def all_rows(self) -> list[dict[str, Any]]:
        return [row for call in self.calls for row in (call["rows"] or [])]


# ---------------------------------------------------------------------------
# EventTranslator.translate_trace
# ---------------------------------------------------------------------------


def test_translator_trace_reasoning_streams_then_finishes() -> None:
    """思考增量攒到阈值即出一段行；ended 只补剩余尾巴，不重复上行内容。"""
    translator = EventTranslator("t1")
    first = translator.translate_trace(
        make_trace_event(
            1, "session.next.reasoning.delta", {"reasoningID": "r1", "delta": "思" * 1300}
        )
    )
    assert len(first) == 1
    assert first[0]["kind"] == "reasoning"
    assert first[0]["payload"]["text"] == "思" * 1300

    # 未达阈值：不出行，仅暂存
    assert translator.translate_trace(
        make_trace_event(2, "session.next.reasoning.delta", {"reasoningID": "r1", "delta": "继续"})
    ) == []

    ended = translator.translate_trace(
        make_trace_event(
            3,
            "session.next.reasoning.ended",
            {"reasoningID": "r1", "text": "思" * 1300 + "继续，结论先行"},
        )
    )
    assert len(ended) == 1
    assert ended[0]["payload"]["text"] == "继续，结论先行"


def test_translator_trace_text_ended_emits_full_text() -> None:
    """正文 ended 携带全文：未上行过增量时整段输出。"""
    translator = EventTranslator("t1")
    rows = translator.translate_trace(
        make_trace_event(
            1, "session.next.text.ended", {"textID": "tx1", "text": "开发完成，共 3 个文件"}
        )
    )
    assert rows == [{"kind": "text", "payload": {"text": "开发完成，共 3 个文件"}}]


def test_translator_trace_tool_lifecycle_correlates_name_by_call_id() -> None:
    """tool.success/failed 不携带工具名：经 callID 回查 tool.called 的名字。"""
    translator = EventTranslator("t1")
    called = translator.translate_trace(
        make_trace_event(
            1,
            "session.next.tool.called",
            {"tool": "bash", "callID": "c1", "input": {"command": "npm test"}},
        )
    )
    assert called == [
        {
            "kind": "tool",
            "payload": {"tool": "bash", "phase": "called", "call_id": "c1", "target": "npm test"},
        }
    ]

    success = translator.translate_trace(
        make_trace_event(
            2,
            "session.next.tool.success",
            {
                "callID": "c1",
                "content": [{"type": "text", "text": "3 passed"}],
                "structured": {"exit": 0},
            },
        )
    )
    assert success == [
        {
            "kind": "tool",
            "payload": {
                "tool": "bash",
                "phase": "success",
                "call_id": "c1",
                "summary": "3 passed",
                "exit_code": 0,
            },
        }
    ]

    # 无 called 前文（如断线丢事件）：工具名回退 unknown，摘要取 error
    failed = translator.translate_trace(
        make_trace_event(
            3,
            "session.next.tool.failed",
            {"callID": "c2", "error": {"type": "unknown", "message": "命令不存在"}},
        )
    )
    assert failed[0]["kind"] == "tool"
    assert failed[0]["payload"]["phase"] == "failed"
    assert failed[0]["payload"]["summary"] == "命令不存在"


def test_translator_trace_ignores_progress_events() -> None:
    """step/todo 等进度事件不产生轨迹行（进度与轨迹通道互不掺和）。"""
    translator = EventTranslator("t1")
    assert translator.translate_trace(
        make_trace_event(1, "session.next.step.started")
    ) == []
    assert translator.translate_trace(
        make_trace_event(2, "todo.updated", {"todos": []})
    ) == []


# ---------------------------------------------------------------------------
# 脱敏与截断
# ---------------------------------------------------------------------------


def test_redact_text_masks_key_patterns() -> None:
    """sk- 串、凭据赋值、Bearer 头统一替换为 ***。"""
    assert "sk-abc123XYZ" not in redact_text("密钥 sk-abc123XYZ9 已配置")
    assert "hunter2" not in redact_text("password=hunter2")
    assert "abcd1234" not in redact_text("token: abcd1234")
    assert "realtokenvalue" not in redact_text("Authorization: Bearer realtokenvalue")


def test_redact_payload_masks_sensitive_keys_recursively() -> None:
    """敏感键名的值整体打码；普通键（如 tokens 用量）不受影响。"""
    payload = redact_payload(
        {
            "api_key": "plain-secret-value",
            "nested": {"token": "leak", "note": "sk-abcdefgh123456"},
            "tokens": {"input": 120},
            "items": ["password=xyz", 1],
        }
    )
    assert payload["api_key"] == "***"
    assert payload["nested"]["token"] == "***"
    assert "sk-abcdefgh123456" not in payload["nested"]["note"]
    assert payload["tokens"] == {"input": 120}
    assert payload["items"][0] == "password=***"
    assert payload["items"][1] == 1


def test_cap_text_keeps_head_and_tail_by_bytes() -> None:
    """超长文本按 UTF-8 字节保留头尾并带截断标记；短文本原样返回。"""
    assert cap_text("短文本") == "短文本"
    long_text = "思" * 5000
    capped = cap_text(long_text)
    assert len(capped.encode("utf-8")) < 8000
    assert "【截断】" in capped
    assert capped.startswith("思")
    assert capped.endswith("思")


# ---------------------------------------------------------------------------
# AgentTraceCollector
# ---------------------------------------------------------------------------


async def test_collector_assigns_monotonic_seq_and_batches() -> None:
    """seq 从 1 单调递增；批量送达，会话元数据透传。"""
    reporter = RecordingReporter()
    collector = AgentTraceCollector("t1", reporter=reporter)
    collector.configure_session("ses_task_t1")
    for index in range(40):
        collector.emit_rows([{"kind": "text", "payload": {"text": f"行{index}"}}])
    # 攒批触发的后台 flush 与直接 flush 并发安全（锁串行），最终全量送达
    await collector.flush()

    rows = reporter.all_rows()
    assert [row["seq"] for row in rows] == list(range(1, 41))
    assert all(call["session_id"] == "ses_task_t1" for call in reporter.calls)
    assert all(call["node_id"] is None for call in reporter.calls)
    assert all(call["task_id"] == "t1" for call in reporter.calls)


async def test_collector_redacts_and_truncates_on_emit() -> None:
    """入队即脱敏/截断：送达的行不含明文密钥、超长文本已收敛。"""
    reporter = RecordingReporter()
    collector = AgentTraceCollector("t1", reporter=reporter)
    collector.emit_rows(
        [
            {
                "kind": "tool",
                "payload": {"tool": "bash", "target": "export KEY=sk-abcdefgh123456"},
            },
            {"kind": "reasoning", "payload": {"text": "思" * 5000}},
        ]
    )
    await collector.flush()

    rows = reporter.all_rows()
    assert "sk-abcdefgh123456" not in str(rows[0])
    assert len(str(rows[1])) < 8000
    assert "【截断】" in rows[1]["payload"]["text"]


async def test_collector_backpressure_drops_oldest_and_notices() -> None:
    """队列满丢最老行；恢复后补一条 status 行告知丢弃数量。"""
    reporter = RecordingReporter()
    collector = AgentTraceCollector("t1", reporter=reporter)
    for index in range(MAX_PENDING_ROWS + 10):
        collector._enqueue("text", {"text": f"行{index}"})
    await collector.flush()

    rows = reporter.all_rows()
    seqs = [row["seq"] for row in rows]
    # 只保留最新 MAX_PENDING_ROWS 行 + 一条背压告知行
    assert len(rows) == MAX_PENDING_ROWS + 1
    assert seqs == sorted(seqs)
    notice = rows[-1]
    assert notice["kind"] == "status"
    assert "丢弃 10 条" in notice["payload"]["message"]


async def test_collector_requeues_batch_after_failure() -> None:
    """上报失败整批回队：下次 flush 按原序重发，行不丢失。"""
    reporter = RecordingReporter(fail_times=1)
    collector = AgentTraceCollector("t1", reporter=reporter)
    collector.emit_rows([{"kind": "text", "payload": {"text": "第一行"}}])
    collector.emit_rows([{"kind": "text", "payload": {"text": "第二行"}}])
    await collector.flush()
    assert reporter.calls == []  # 首次失败，行已回队

    collector.emit_rows([{"kind": "text", "payload": {"text": "第三行"}}])
    await collector.flush()

    rows = reporter.all_rows()
    assert [row["payload"]["text"] for row in rows] == ["第一行", "第二行", "第三行"]
    assert [row["seq"] for row in rows] == [1, 2, 3]


async def test_collector_close_drains_pending_and_never_raises() -> None:
    """close 排水全部积压；reporter 持续失败时 close 不抛异常。"""
    reporter = RecordingReporter(fail_times=99)
    collector = AgentTraceCollector("t1", reporter=reporter)
    collector.emit_rows([{"kind": "text", "payload": {"text": "唯一一行"}}])
    await collector.close()  # 不应抛出
    assert reporter.calls == []
    assert len(collector._pending) == 1  # 失败批次保留等待重试


async def test_collector_flush_failure_does_not_break_caller() -> None:
    """红线：上报异常只记日志，不向调用方（消费循环/桥接层）传播。"""
    async def exploding(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("中枢炸了")

    collector = AgentTraceCollector("t1", reporter=exploding)
    collector.emit_rows([{"kind": "text", "payload": {"text": "内容"}}])
    await asyncio.wait_for(collector.flush(), timeout=1.0)  # 未抛出即通过
