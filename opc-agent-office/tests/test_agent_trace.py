"""Agent 轨迹镜像接入测试（ADR-065 M5）。

覆盖四组能力：

- **UT-M5-1**：seq 按任务从 1 单调递增、四类 ``kind``（status/reasoning/text/tool）
  分类正确、阶段变更点各只发一条 ``status`` 行；
- **UT-M5-2**：轨迹上报抛异常时任务照常完成（completed 正常上报）且只记 warning；
- **UT-M5-3**：``agent.task_verify_blocked`` 断线期间进离线缓冲，重连后在待发队列
  并补发（payload 与编程节点逐字对齐）；
- **脱敏**：``sk-`` 形态串与 ``api_key``/``token`` 等敏感键值在落库前替换为 ``***``。

另有 hub_client 轨迹上报的旁路语义（空行不发请求 / 失败只 warning / 请求体形状）
与 session_id 对齐用例。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from app.client import hub_client as hub_mod
from app.client.hub_client import HubClient
from app.config import AgentConfig
from app.executor.office_bridge import (
    BridgeCallbacks,
    OfficeBridge,
    TaskRunState,
    office_session_id,
    redact_trace_payload,
)
from app.executor.verify import VerifyItem, VerifyReport
from app.models.schemas import TaskDispatchPayload
from app.tools.base import ToolError, ToolRegistry


def make_config(**overrides: object) -> AgentConfig:
    values = {
        "AGENT_REGISTRATION_TOKEN": "t",
        "WORKSPACE_ROOT": "",
        "OFFICE_LLM_MAX_ROUNDS": "10",
    }
    values.update({k: str(v) for k, v in overrides.items()})
    import os

    for key, value in values.items():
        os.environ[key] = value
    return AgentConfig(_env_file=None)


def make_dispatch(task_id: str, output_dir: Path | str) -> TaskDispatchPayload:
    return TaskDispatchPayload.model_validate(
        {
            "task_id": task_id,
            "task_name": "写调研报告",
            "description": "调研某主题并产出 docx",
            "executor_model": {
                "source": "hub",
                "model": "deepseek/deepseek-chat",
                "provider": "deepseek",
                "api_key": "sk-test-123",
                "base_url": "https://api.deepseek.com",
            },
            "output_dir": str(output_dir),
        }
    )


def tool_call(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


class ScriptedLLM:
    """脚本化 LLM：按预设响应序列返回 assistant message。"""

    model = "fake-model"

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.rounds = 0

    async def chat(self, messages, tools=None):  # noqa: ANN001, ANN202
        self.rounds += 1
        if not self.responses:
            return {
                "message": {"role": "assistant", "content": "完成。"},
                "prompt_tokens": 10,
                "completion_tokens": 5,
            }
        return {
            "message": self.responses.pop(0),
            "prompt_tokens": 10,
            "completion_tokens": 5,
        }


def install_fake_llm(monkeypatch: pytest.MonkeyPatch, *scripted: ScriptedLLM) -> None:
    """把桥接层构造的 LLMClient 换成脚本化替身（每个任务消费一个脚本）。"""
    import app.executor.office_bridge as bridge_mod

    queue = list(scripted)

    class FakeLLMClient:
        def __init__(self, **kwargs: Any) -> None:
            self.model = kwargs.get("model", "fake-model")
            self._scripted = queue.pop(0) if queue else ScriptedLLM([])

        async def chat(self, messages, tools=None):  # noqa: ANN001, ANN202
            return await self._scripted.chat(messages, tools)

    monkeypatch.setattr(bridge_mod, "LLMClient", FakeLLMClient)


class TraceCallbacks(BridgeCallbacks):
    """收集上报事件与轨迹批次的假回调（``trace_error`` 模拟上报失败）。"""

    def __init__(self, trace_error: Exception | None = None) -> None:
        self.events: list[tuple] = []
        self.trace_calls: list[dict[str, Any]] = []
        self.calls: dict[str, int] = {}
        self._trace_error = trace_error

    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    async def on_progress(
        self, task_id, progress, todos=None, *, stage=None, message=None, tool=None, detail=None
    ):  # noqa: ANN001, ANN202
        self._count("progress")
        self.events.append(("progress", task_id, progress, stage, message))

    async def on_task_started(self, task_id, executor_model_actual):  # noqa: ANN001, ANN202
        self._count("started")
        self.events.append(("started", task_id, executor_model_actual))

    async def on_completed(
        self, task_id, session_id, plan_content=None, change_summary=None, project_context=None
    ):  # noqa: ANN001, ANN202
        self._count("completed")
        self.events.append(("completed", task_id, session_id, change_summary))

    async def on_failed(self, task_id, reason):  # noqa: ANN001, ANN202
        self._count("failed")
        self.events.append(("failed", task_id, reason))

    async def on_file_tree(self, task_id, files, truncated):  # noqa: ANN001, ANN202
        self._count("file_tree")
        self.events.append(("file_tree", task_id, len(files), truncated))

    async def on_file_chunk(self, request_id, seq, total, data_b64, done):  # noqa: ANN001, ANN202
        self._count("file_chunk")

    async def on_file_error(self, request_id, reason):  # noqa: ANN001, ANN202
        self._count("file_error")

    async def on_agent_trace(self, task_id, *, node_id, session_id, rows):  # noqa: ANN001, ANN202
        """模拟 HubClient.report_agent_trace 的同签名旁路上报。"""
        self._count("trace")
        self.trace_calls.append(
            {"task_id": task_id, "node_id": node_id, "session_id": session_id, "rows": rows}
        )
        if self._trace_error is not None:
            raise self._trace_error


def all_rows(callbacks: TraceCallbacks, task_id: str | None = None) -> list[dict[str, Any]]:
    """按上报顺序摊平全部轨迹行（可按任务过滤）。"""
    return [
        row
        for call in callbacks.trace_calls
        if task_id is None or call["task_id"] == task_id
        for row in call["rows"]
    ]


def prepare_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scripted: ScriptedLLM,
    callbacks: TraceCallbacks,
) -> OfficeBridge:
    """装配桥接：假 LLM + 已绑定轨迹通道。"""
    bridge = OfficeBridge(make_config(WORKSPACE_ROOT=str(tmp_path)), callbacks)
    bridge.bind_agent_trace(callbacks.on_agent_trace)
    install_fake_llm(monkeypatch, scripted)
    return bridge


# --------------------------------------------------------------------------- #
# UT-M5-1：seq 单调 + 四类 kind + 阶段变更点只发一次
# --------------------------------------------------------------------------- #


async def test_ut_m5_1_seq_monotonic_and_kinds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """UT-M5-1：seq 按任务从 1 单调递增，四类 kind 分类正确（不多发别的 kind）。"""
    scripted = ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "先写交付文档",
                "tool_calls": [
                    tool_call(
                        "c1",
                        "write_docx",
                        {
                            "filename": "产出.docx",
                            "title": "交付文档",
                            "blocks": [{"type": "paragraph", "text": "正文"}],
                        },
                    )
                ],
            },
            {"role": "assistant", "content": "文档已生成，核心结论 XYZ。"},
        ]
    )
    callbacks = TraceCallbacks()
    bridge = prepare_bridge(tmp_path, monkeypatch, scripted, callbacks)

    await bridge.execute_task(make_dispatch("task-abc-001", tmp_path / "ws"))

    rows = all_rows(callbacks)
    seqs = [row["seq"] for row in rows]
    # 从 1 起、步长 1、无空洞：既是单调递增也是完整序列
    assert seqs == list(range(1, len(rows) + 1))
    kinds = {row["kind"] for row in rows}
    assert {"status", "reasoning", "text", "tool"} <= kinds
    # 只发中枢白名单内的类型（办公节点不产出 todo 行）；
    # ADR-065 M7 起任务结束另有 1 条 usage 用量行
    assert kinds <= {"status", "reasoning", "text", "tool", "usage"}
    # status 行 payload 三要素齐备
    status_rows = [row for row in rows if row["kind"] == "status"]
    assert status_rows
    assert all({"stage", "message", "progress"} <= set(row["payload"]) for row in status_rows)
    # 工具行 payload 四要素齐备
    tool_rows = [row for row in rows if row["kind"] == "tool"]
    assert len(tool_rows) == 1
    assert tool_rows[0]["payload"]["tool"] == "write_docx"
    assert tool_rows[0]["payload"]["ok"] is True
    assert "产出.docx" in tool_rows[0]["payload"]["args_summary"]
    assert tool_rows[0]["payload"]["result_summary"]


async def test_ut_m5_1_status_only_on_stage_change(tmp_path: Path) -> None:
    """UT-M5-1：阶段变更点各只发一条 status 行（同阶段重复进度不重复发）。"""
    callbacks = TraceCallbacks()
    bridge = OfficeBridge(make_config(WORKSPACE_ROOT=str(tmp_path)), callbacks)
    bridge.bind_agent_trace(callbacks.on_agent_trace)
    state = TaskRunState("task-s1")

    await bridge._report_progress(state, 10, "planning", "正在规划任务大纲")  # noqa: SLF001
    await bridge._report_progress(state, 12, "planning", "正在规划任务大纲")  # noqa: SLF001
    stages = [row["payload"]["stage"] for row in all_rows(callbacks)]
    assert stages == ["planning"]

    await bridge._report_progress(state, 40, "coding", "正在编写交付文档")  # noqa: SLF001
    status_rows = [row for row in all_rows(callbacks) if row["kind"] == "status"]
    assert [row["payload"]["stage"] for row in status_rows] == ["planning", "coding"]
    assert status_rows[-1]["payload"]["progress"] == 40
    assert status_rows[-1]["payload"]["message"] == "正在编写交付文档"
    # status 行即时上报：两次阶段变更 = 两批
    assert len(callbacks.trace_calls) == 2


# --------------------------------------------------------------------------- #
# UT-M5-2：轨迹上报失败不影响任务
# --------------------------------------------------------------------------- #


async def test_ut_m5_2_trace_failure_keeps_task_completing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """UT-M5-2：上报抛异常 → 任务照常完成（completed 正常）且只记 warning。"""
    scripted = ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    tool_call(
                        "c1",
                        "write_docx",
                        {
                            "filename": "产出.docx",
                            "title": "交付文档",
                            "blocks": [{"type": "paragraph", "text": "正文"}],
                        },
                    )
                ],
            },
            {"role": "assistant", "content": "文档已生成。"},
        ]
    )
    callbacks = TraceCallbacks(trace_error=RuntimeError("中枢 500"))
    bridge = prepare_bridge(tmp_path, monkeypatch, scripted, callbacks)

    with caplog.at_level(logging.WARNING):
        await bridge.execute_task(make_dispatch("task-abc-002", tmp_path / "ws"))

    assert callbacks.calls.get("completed") == 1
    assert "failed" not in callbacks.calls
    # 进度上报不受影响（5 次以上：准备/规划/工具/总结/核对/收尾）
    assert callbacks.calls["progress"] >= 4
    assert callbacks.calls["trace"] >= 1  # 确实发起过上报
    trace_warnings = [r for r in caplog.records if r.getMessage() == "agent_trace_report_failed"]
    assert trace_warnings
    assert all(r.levelno == logging.WARNING for r in trace_warnings)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    # 收尾清空：seq 计数与待发队列都不残留
    assert bridge._trace_seq == {}  # noqa: SLF001
    assert bridge._trace_pending == {}  # noqa: SLF001


# --------------------------------------------------------------------------- #
# UT-M5-3：验收阻断事件离线缓冲
# --------------------------------------------------------------------------- #


async def test_ut_m5_3_verify_blocked_buffered_offline() -> None:
    """UT-M5-3：agent.task_verify_blocked 断线入缓冲，重连后仍在待发队列并补发。"""
    assert "agent.task_verify_blocked" in hub_mod._BUFFERED_EVENTS  # noqa: SLF001
    client = HubClient(make_config(HUB_API_URL="http://hub.test:8000"))

    # 未连接（websocket is None）→ 事件进入离线缓冲，而不是被丢弃
    await client.report_verify_blocked(
        "task-1",
        session_id="office_task-1",
        reason="L1 内容闸未通过",
        report={"summary": "L1 内容闸未通过", "repair_rounds": 5},
    )
    assert [event for event, _ in client._pending_events] == [  # noqa: SLF001
        "agent.task_verify_blocked"
    ]

    # 重连：冲刷缓冲，事件按序补发
    sent: list[dict[str, Any]] = []

    class FakeWebSocket:
        state = SimpleNamespace(name="OPEN")

        async def send(self, message: str) -> None:
            sent.append(json.loads(message))

    client.websocket = FakeWebSocket()  # type: ignore[assignment]
    await client._flush_pending_events()  # noqa: SLF001

    assert len(client._pending_events) == 0  # noqa: SLF001
    assert sent and sent[0]["event"] == "agent.task_verify_blocked"
    # 字段与编程节点 report_verify_blocked 逐字对齐
    assert sent[0]["payload"] == {
        "task_id": "task-1",
        "session_id": "office_task-1",
        "reason": "L1 内容闸未通过",
        "report": {"summary": "L1 内容闸未通过", "repair_rounds": 5},
    }


async def test_verify_blocked_uses_hub_dedicated_method(tmp_path: Path) -> None:
    """桥接层优先走 HubClient.report_verify_blocked（关键字入参，字段对齐编程节点）。"""

    class FakeHubOwner:
        """假 HubClient：提供 on_failed 绑定方法 + 专用阻断上报方法。"""

        def __init__(self) -> None:
            self.verify_calls: list[dict[str, Any]] = []

        async def on_failed(self, task_id: str, reason: str) -> None:
            pass

        async def report_verify_blocked(
            self,
            task_id: str,
            *,
            session_id: str | None = None,
            reason: str = "",
            report: dict[str, Any] | None = None,
        ) -> None:
            self.verify_calls.append(
                {
                    "task_id": task_id,
                    "session_id": session_id,
                    "reason": reason,
                    "report": report,
                }
            )

    owner = FakeHubOwner()
    callbacks = TraceCallbacks()
    callbacks.on_failed = owner.on_failed  # type: ignore[method-assign]
    bridge = OfficeBridge(make_config(WORKSPACE_ROOT=str(tmp_path)), callbacks)
    report = VerifyReport(
        passed=False,
        layer="L1",
        items=[VerifyItem("L1-C1", "L1", "结论章节必须存在", False, reason="缺失结论章节")],
        repair_round=5,
    )

    await bridge._report_verify_blocked("task-xyz", report)  # noqa: SLF001

    assert len(owner.verify_calls) == 1
    call = owner.verify_calls[0]
    assert call["task_id"] == "task-xyz"
    assert call["session_id"] == office_session_id("task-xyz")
    assert "L1" in call["reason"]
    assert call["report"] is not None and call["report"]["status"] == "failed"


# --------------------------------------------------------------------------- #
# 脱敏
# --------------------------------------------------------------------------- #


def test_redact_trace_payload_unit() -> None:
    """脱敏函数：sk- 串 / api_key / token / password 替换为 ***，用量字段不误伤。"""
    payload = {
        "api_key": "sk-abc123456789",
        "note": "调用时用 sk-xyz789012345 与 token=abc12345678",
        "password": "p@ssw0rd",
        "nested": [{"access_token": "t-9"}, {"tokens": 1234}, {"text": "sk-qqqqqqqqqq"}],
    }

    redacted = redact_trace_payload(payload)
    dumped = json.dumps(redacted, ensure_ascii=False)

    assert redacted["api_key"] == "***"
    assert redacted["password"] == "***"
    assert redacted["nested"][0]["access_token"] == "***"
    assert redacted["nested"][1]["tokens"] == 1234  # 用量字段不被误伤
    assert "sk-abc123456789" not in dumped
    assert "sk-xyz789012345" not in dumped
    assert "sk-qqqqqqqqqq" not in dumped
    assert "abc12345678" not in dumped
    assert "t-9" not in dumped


async def test_secret_never_reaches_trace_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """端到端：工具入参里的凭据在落库前已被抹掉（上报行里绝无原文）。"""

    class SecretTool:
        """假检索工具：入参故意带凭据，用于验证轨迹脱敏（真实工具不需要）。"""

        name = "web_search"
        description = "假检索工具"
        parameters: dict[str, Any] = {}

        def __init__(self, work_dir: Path) -> None:
            self._work_dir = work_dir

        def to_openai_schema(self) -> dict[str, Any]:
            return {"type": "function", "function": {"name": self.name, "parameters": {}}}

        def execute(self, args: dict[str, Any]) -> str:
            return f"搜索完成：{args.get('query', '')}"

    import app.executor.office_bridge as bridge_mod

    def fake_registry(work_dir: Path, config: AgentConfig) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(SecretTool(work_dir))
        return registry

    monkeypatch.setattr(bridge_mod, "default_registry", fake_registry)

    scripted = ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    tool_call(
                        "c1",
                        "web_search",
                        {
                            "query": "季度报表",
                            "api_key": "sk-secret-123456",
                            "note": "token=abc12345678",
                        },
                    )
                ],
            },
            {"role": "assistant", "content": "资料已汇总。"},
        ]
    )
    callbacks = TraceCallbacks()
    bridge = prepare_bridge(tmp_path, monkeypatch, scripted, callbacks)

    await bridge.execute_task(make_dispatch("task-abc-003", tmp_path / "ws"))

    dumped = json.dumps(all_rows(callbacks), ensure_ascii=False)
    assert "sk-secret-123456" not in dumped
    assert "abc12345678" not in dumped
    assert "***" in dumped
    tool_rows = [row for row in all_rows(callbacks) if row["kind"] == "tool"]
    assert tool_rows and "sk-" not in tool_rows[0]["payload"]["args_summary"]


# --------------------------------------------------------------------------- #
# reasoning / text 两类行
# --------------------------------------------------------------------------- #


async def test_reasoning_and_text_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """有思考字段发 reasoning+text；无思考字段只发 text（不伪造空 reasoning 行）。"""
    with_reasoning = ScriptedLLM(
        [{"role": "assistant", "content": "结论 A。", "reasoning_content": "先查资料再下结论"}]
    )
    callbacks_a = TraceCallbacks()
    bridge_a = prepare_bridge(tmp_path, monkeypatch, with_reasoning, callbacks_a)
    await bridge_a.execute_task(make_dispatch("task-r1", tmp_path / "ws-r1"))

    kinds_a = [row["kind"] for row in all_rows(callbacks_a)]
    assert "reasoning" in kinds_a and "text" in kinds_a
    reasoning_row = next(row for row in all_rows(callbacks_a) if row["kind"] == "reasoning")
    assert reasoning_row["payload"]["text"] == "先查资料再下结论"
    assert reasoning_row["payload"]["round"] == 1

    without_reasoning = ScriptedLLM([{"role": "assistant", "content": "结论 B。"}])
    callbacks_b = TraceCallbacks()
    bridge_b = prepare_bridge(tmp_path, monkeypatch, without_reasoning, callbacks_b)
    await bridge_b.execute_task(make_dispatch("task-r2", tmp_path / "ws-r2"))

    rows_b = all_rows(callbacks_b)
    assert not [row for row in rows_b if row["kind"] == "reasoning"]
    assert [row for row in rows_b if row["kind"] == "text"]


# --------------------------------------------------------------------------- #
# 工具行 ok 标记
# --------------------------------------------------------------------------- #


async def test_tool_row_marks_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """工具失败（ToolError）→ tool 行 ok=False，且任务循环继续到最终答复。"""

    class BrokenTool:
        name = "web_search"
        description = "总是失败的检索工具"
        parameters: dict[str, Any] = {}

        def __init__(self, work_dir: Path) -> None:
            pass

        def to_openai_schema(self) -> dict[str, Any]:
            return {"type": "function", "function": {"name": self.name, "parameters": {}}}

        def execute(self, args: dict[str, Any]) -> str:
            raise ToolError("上游 502")

    import app.executor.office_bridge as bridge_mod

    def fake_registry(work_dir: Path, config: AgentConfig) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(BrokenTool(work_dir))
        return registry

    monkeypatch.setattr(bridge_mod, "default_registry", fake_registry)
    scripted = ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [tool_call("c1", "web_search", {"query": "x"})],
            },
            {"role": "assistant", "content": "检索失败，改用已有资料。"},
        ]
    )
    callbacks = TraceCallbacks()
    bridge = prepare_bridge(tmp_path, monkeypatch, scripted, callbacks)

    await bridge.execute_task(make_dispatch("task-abc-004", tmp_path / "ws"))

    tool_rows = [row for row in all_rows(callbacks) if row["kind"] == "tool"]
    assert len(tool_rows) == 1
    assert tool_rows[0]["payload"]["ok"] is False
    assert "上游 502" in tool_rows[0]["payload"]["result_summary"]
    assert callbacks.calls.get("completed") == 1


# --------------------------------------------------------------------------- #
# 任务起止行 + seq 清空 + session_id 对齐
# --------------------------------------------------------------------------- #


async def test_task_bounds_rows_and_seq_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """起止各一条 status 行；任务结束清空 seq，下一个任务重新从 1 开始。"""
    scripted_a = ScriptedLLM([{"role": "assistant", "content": "任务 A 完成。"}])
    scripted_b = ScriptedLLM([{"role": "assistant", "content": "任务 B 完成。"}])
    callbacks = TraceCallbacks()
    bridge = OfficeBridge(make_config(WORKSPACE_ROOT=str(tmp_path)), callbacks)
    bridge.bind_agent_trace(callbacks.on_agent_trace)
    install_fake_llm(monkeypatch, scripted_a, scripted_b)

    await bridge.execute_task(make_dispatch("task-a-1111", tmp_path / "ws-a"))
    rows_a = all_rows(callbacks, "task-a-1111")
    assert rows_a[0]["payload"] == {
        "stage": "preparing",
        "message": "任务开始执行",
        "progress": 0,
    }
    assert rows_a[-1]["payload"]["message"] == "任务执行结束"
    assert rows_a[-1]["payload"]["stage"] == "summarizing"
    assert [row["seq"] for row in rows_a] == list(range(1, len(rows_a) + 1))
    assert bridge._trace_seq == {}  # noqa: SLF001
    assert bridge._trace_pending == {}  # noqa: SLF001

    await bridge.execute_task(make_dispatch("task-b-2222", tmp_path / "ws-b"))
    rows_b = all_rows(callbacks, "task-b-2222")
    assert rows_b[0]["seq"] == 1  # 新任务重新从 1 开始
    assert [row["seq"] for row in rows_b] == list(range(1, len(rows_b) + 1))


async def test_trace_session_id_matches_completed_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """轨迹行的 session_id 与 agent.task_completed 携带值逐字一致（任务内稳定）。"""
    scripted = ScriptedLLM([{"role": "assistant", "content": "搞定。"}])
    callbacks = TraceCallbacks()
    bridge = prepare_bridge(tmp_path, monkeypatch, scripted, callbacks)

    await bridge.execute_task(make_dispatch("task-abc-005", tmp_path / "ws"))

    expected = office_session_id("task-abc-005")
    assert expected == "office_task-abc"
    assert {call["session_id"] for call in callbacks.trace_calls} == {expected}
    completed = [event for event in callbacks.events if event[0] == "completed"][0]
    assert completed[2] == expected


async def test_off_whitelist_kind_dropped(tmp_path: Path) -> None:
    """白名单外的 kind 不发（中枢只会计入 ignored，白占单任务行数配额）。"""
    callbacks = TraceCallbacks()
    bridge = OfficeBridge(make_config(WORKSPACE_ROOT=str(tmp_path)), callbacks)
    bridge.bind_agent_trace(callbacks.on_agent_trace)

    await bridge._report_trace("todo", {"content": "不该发出去"}, task_id="task-w1")  # noqa: SLF001
    assert callbacks.trace_calls == []

    await bridge._report_trace(  # noqa: SLF001
        "status", {"stage": "planning"}, task_id="task-w1", immediate=True
    )
    assert [row["kind"] for row in all_rows(callbacks)] == ["status"]


async def test_trace_text_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """超长文本按节点侧上限截断（中枢 8KB 单行截断红线之前先自我收敛）。"""
    long_content = "长" * 5000
    scripted = ScriptedLLM([{"role": "assistant", "content": long_content}])
    callbacks = TraceCallbacks()
    bridge = prepare_bridge(tmp_path, monkeypatch, scripted, callbacks)

    await bridge.execute_task(make_dispatch("task-cap-001", tmp_path / "ws"))

    text_rows = [row for row in all_rows(callbacks) if row["kind"] == "text"]
    assert text_rows
    text = text_rows[-1]["payload"]["text"]
    assert len(text) < 5000
    assert text.endswith("【截断】")


# --------------------------------------------------------------------------- #
# hub_client 旁路语义
# --------------------------------------------------------------------------- #


@respx.mock
async def test_report_agent_trace_skips_empty_rows() -> None:
    """rows 为空不发请求（旁路零开销）。"""
    client = HubClient(make_config(HUB_API_URL="http://hub.test:8000"))
    route = respx.post("http://hub.test:8000/api/v1/tasks/task-1/agent-trace")

    await client.report_agent_trace("task-1", node_id=None, session_id=None, rows=[])

    assert route.call_count == 0


@respx.mock
async def test_report_agent_trace_failure_only_warns(caplog: pytest.LogCaptureFixture) -> None:
    """上报失败只记 warning（agent_trace_report_failed），绝不上抛。"""
    client = HubClient(make_config(HUB_API_URL="http://hub.test:8000"))
    respx.post("http://hub.test:8000/api/v1/tasks/task-1/agent-trace").mock(
        return_value=httpx.Response(500, text="boom")
    )

    with caplog.at_level(logging.WARNING):
        await client.report_agent_trace(
            "task-1",
            node_id="node-9",
            session_id="office_task-1",
            rows=[{"seq": 1, "kind": "status", "payload": {"stage": "planning"}}],
        )

    assert any(r.getMessage() == "agent_trace_report_failed" for r in caplog.records)


@respx.mock
async def test_report_agent_trace_request_shape() -> None:
    """请求体形状：node_id 回退本节点 ID，session_id/rows 原样透传。"""
    client = HubClient(make_config(HUB_API_URL="http://hub.test:8000"))
    client.node_id = "node-42"
    route = respx.post("http://hub.test:8000/api/v1/tasks/task-1/agent-trace").mock(
        return_value=httpx.Response(
            200, json={"code": 0, "message": "ok", "data": {"accepted": 1, "ignored": 0}}
        )
    )
    rows = [{"seq": 1, "kind": "status", "payload": {"stage": "planning"}}]

    await client.report_agent_trace(
        "task-1", node_id=None, session_id="office_task-1", rows=rows
    )

    assert route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert body == {"node_id": "node-42", "session_id": "office_task-1", "rows": rows}


async def test_task_bridge_binding_wires_trace(tmp_path: Path) -> None:
    """装配处绑定：``client.task_bridge = bridge`` 后轨迹通道自动挂上（main.py 无需改）。"""
    client = HubClient(make_config(HUB_API_URL="http://hub.test:8000"))
    callbacks = TraceCallbacks()
    callbacks.on_agent_trace = None  # 模拟未绑定状态（BridgeCallbacks 缺省即 None）
    bridge = OfficeBridge(make_config(WORKSPACE_ROOT=str(tmp_path)), callbacks)

    assert callbacks.on_agent_trace is None
    client.task_bridge = bridge
    assert client.task_bridge is bridge
    assert callbacks.on_agent_trace == client.report_agent_trace

    # 桥接替身未实现 bind_agent_trace 时不得报错（轨迹是旁路）
    client.task_bridge = object()  # type: ignore[assignment]
    assert client.task_bridge is not None
