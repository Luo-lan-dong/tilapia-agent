"""成本护栏与用量可见单元测试（ADR-065 M7）。

覆盖：

- **UT-M7-1**：用量达软阈值（默认预算 80%）→ 注入「收尾纪律」系统提示
  + 发一条 ``status`` 轨迹行（payload 含 budget_percent/stage/message）
  + 记 ``office_budget_soft_limit`` warning，**任务继续并正常完成**；
- **UT-M7-1b**：重复跨越软阈值**不重复**注入 / 不重复上报（幂等）；
- **UT-M7-2**：用量达 100% → **硬停**（不再调用 LLM）、
  ``stopped_reason="token_budget"``、桥接层上报 ``budget_hard_exceeded`` 明确原因
  且**不假装成功**（无 completed）；
- **UT-M7-3**：任务结束发一条 ``usage`` 轨迹行且字段齐全（prompt_tokens/
  completion_tokens/rounds/budget_percent）；上报失败只 warning、任务照常完成。

另：阈值可由配置覆盖（``OFFICE_LLM_BUDGET_SOFT_RATIO``）生效，且任务结束时
统计的是**跨执行累计**用量（首轮 + 各回灌轮）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest

import app.executor.office_bridge as bridge_mod
from app.config import AgentConfig
from app.executor.office_bridge import BridgeCallbacks, OfficeBridge, office_session_id
from app.executor.tool_loop import ToolLoopRunner
from app.models.schemas import TaskDispatchPayload
from app.tools.base import ToolRegistry

#: 脚本化 LLM 每次响应的固定用量（prompt 600 + completion 400 = 1000）
_PROMPT_TOKENS = 600
_COMPLETION_TOKENS = 400
_TOKENS_PER_CALL = _PROMPT_TOKENS + _COMPLETION_TOKENS


def make_config(**overrides: object) -> AgentConfig:
    """构造节点配置（环境变量显式覆盖，避免用例间串味）。

    注意 ``OFFICE_LLM_TOKEN_BUDGET`` 的下界是 10000（配置校验），故预算取 10000、
    每次调用 1000 tokens：软阈值 80% = 第 8 次调用后跨过，硬阈值 100% = 第 10 次。
    """
    values = {
        "AGENT_REGISTRATION_TOKEN": "t",
        "WORKSPACE_ROOT": "",
        "OFFICE_LLM_MAX_ROUNDS": "30",
        "OFFICE_LLM_TOKEN_BUDGET": "10000",
        "OFFICE_LLM_BUDGET_SOFT_RATIO": "0.8",
    }
    values.update({key: str(value) for key, value in overrides.items()})
    for key, value in values.items():
        assert isinstance(value, str)  # 环境变量必须是字符串（防手滑传 int）
        os.environ[key] = value
    return AgentConfig(_env_file=None)


class FakeSearchTool:
    """假检索工具：让每轮都产生一次工具调用（不触网、不落盘）。"""

    name = "web_search"
    description = "假检索工具"
    parameters: dict[str, Any] = {}

    def __init__(self, work_dir: Path) -> None:
        self._work_dir = work_dir
        self.calls = 0

    def to_openai_schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "parameters": {}}}

    def execute(self, args: dict[str, Any]) -> str:
        self.calls += 1
        return f"搜索完成：{args.get('query', '')}"


def tool_call(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


class RecordingLLM:
    """脚本化 LLM：按预设轮数返回工具调用，最后给最终答复；记录每次入参。"""

    model = "fake-model"

    def __init__(
        self, tool_rounds: int, final: str = "最终总结：已交付。", trailing: int = 0
    ) -> None:
        self.tool_rounds = tool_rounds
        self.final = final
        #: 工具轮用尽后的额外答复（回灌轮等）；不足时统一返回「完成。」
        self.trailing = trailing
        self.calls = 0
        self.messages_seen: list[list[dict[str, Any]]] = []

    async def chat(self, messages, tools=None):  # noqa: ANN001, ANN202
        self.calls += 1
        # 深拷贝快照：循环始终复用同一个 messages 列表，不拷贝会记录到同一对象引用
        self.messages_seen.append([dict(message) for message in messages])
        if self.calls <= self.tool_rounds:
            message: dict[str, Any] = {
                "role": "assistant",
                "content": "",
                "tool_calls": [tool_call(f"c{self.calls}", "web_search", {"query": "资料"})],
            }
        elif self.calls == self.tool_rounds + 1:
            message = {"role": "assistant", "content": self.final}
        else:
            message = {"role": "assistant", "content": "完成。"}
        return {
            "message": message,
            "prompt_tokens": _PROMPT_TOKENS,
            "completion_tokens": _COMPLETION_TOKENS,
        }


def make_registry(work_dir: Path, config: AgentConfig) -> ToolRegistry:
    """假工具注册表（与 ``default_registry`` 同签名，供桥接层替换用）。"""
    registry, _tool = _registry_with(work_dir)
    return registry


def _registry_with(work_dir: Path) -> tuple[ToolRegistry, FakeSearchTool]:
    """假工具注册表 + 检索工具（断言工具调用次数用）。"""
    tool = FakeSearchTool(work_dir)
    registry = ToolRegistry()
    registry.register(tool)
    return registry, tool


def collect_progress() -> tuple[list[tuple[int, str, str]], Any]:
    """进度收集器：返回 (steps, on_progress)。"""
    steps: list[tuple[int, str, str]] = []

    async def on_progress(progress: int, stage: str, message: str) -> None:
        steps.append((progress, stage, message))

    return steps, on_progress


def collect_trace() -> tuple[list[tuple[str, dict[str, Any]]], Any]:
    """轨迹收集器：返回 (rows, on_trace)。"""
    rows: list[tuple[str, dict[str, Any]]] = []

    async def on_trace(kind: str, payload: dict[str, Any]) -> None:
        rows.append((kind, payload))

    return rows, on_trace


def normalize_rows(
    rows: list[Any],
) -> list[tuple[str, dict[str, Any]]]:
    """统一轨迹行形状：工具循环回调是 ``(kind, payload)`` 元组，桥接层是行字典。"""
    normalized: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        if isinstance(row, tuple):
            normalized.append((row[0], row[1]))
        else:
            normalized.append((str(row["kind"]), dict(row["payload"])))
    return normalized


def soft_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """筛出软阈值 status 行。"""
    return [
        payload
        for kind, payload in normalize_rows(rows)
        if kind == "status" and payload.get("stage") == "budget_soft_limit"
    ]


def hard_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """筛出硬停 status 行。"""
    return [
        payload
        for kind, payload in normalize_rows(rows)
        if kind == "status" and payload.get("stage") == "budget_hard_stop"
    ]


#: 收尾纪律提示的固定首行（注入检测用；不含主系统提示词里的任何片段）
_SOFT_MARKER = "系统提示（成本护栏·已进入收尾）"


def wrap_up_injections(llm: RecordingLLM) -> int:
    """「收尾纪律」系统提示在**最终**消息列表里的出现次数。

    注意：工具循环始终复用同一个 messages 列表（每次调用记录的是同一对象引用），
    故「被注入了几次」等价于最终列表里该提示出现的条数，不能按调用次数累加。
    """
    if not llm.messages_seen:
        return 0
    return sum(
        1
        for message in llm.messages_seen[-1]
        if message.get("role") == "system" and _SOFT_MARKER in str(message.get("content") or "")
    )


def first_injection_round(llm: RecordingLLM) -> int:
    """首次「看得见」收尾纪律提示的调用序号（1 起；未注入返回 -1）。"""
    for index, messages in enumerate(llm.messages_seen):
        if any(
            message.get("role") == "system"
            and _SOFT_MARKER in str(message.get("content") or "")
            for message in messages
        ):
            return index + 1
    return -1


def warnings(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    """筛出指定事件名的 warning 记录。"""
    return [record for record in caplog.records if record.getMessage() == name]


# --------------------------------------------------------------------------- #
# UT-M7-1：软阈值（80%）→ 收尾注入 + status 轨迹行 + warning，任务继续
# --------------------------------------------------------------------------- #


async def test_ut_m7_1_soft_limit_wraps_up_and_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """UT-M7-1：跨过 80% → 只注入一次收尾纪律、发一条 status 行、记 warning，任务继续完成。"""
    config = make_config()  # 预算 10000，软阈值 8000 = 第 8 次调用（8×1000）后跨过
    llm = RecordingLLM(tool_rounds=8, final="最终总结：资料已汇总。")
    registry, tool = _registry_with(tmp_path)
    steps, on_progress = collect_progress()
    rows, on_trace = collect_trace()
    runner = ToolLoopRunner(
        config, llm, registry, on_progress=on_progress, on_trace=on_trace
    )

    with caplog.at_level(logging.WARNING):
        outcome = await runner.run("task-m7-1", "查资料并汇总", asyncio.Event())

    # 任务继续并完成（不是硬停、不阻断）
    assert outcome.stopped_reason == "final_answer"
    assert outcome.summary == "最终总结：资料已汇总。"
    assert outcome.rounds == 9
    assert outcome.prompt_tokens + outcome.completion_tokens == 9 * _TOKENS_PER_CALL
    assert tool.calls == 8  # 8 轮工具调用都真实执行了
    assert any(stage == "summarizing" for _progress, stage, _message in steps)

    # 收尾纪律已注入（软阈值后仍继续，刚好一次）
    assert wrap_up_injections(llm) == 1
    assert first_injection_round(llm) == 9  # 第 8 次调用后注入 → 第 9 次起可见

    # status 轨迹行：标明已进入收尾，payload 三要素齐备
    soft = soft_rows(rows)
    assert len(soft) == 1
    assert {"budget_percent", "stage", "message"} <= set(soft[0])
    assert soft[0]["budget_percent"] == 80  # 跨过软阈值的那一刻（8000/10000）
    assert "收尾" in soft[0]["message"]
    assert not hard_rows(rows)

    # warning 日志：事件名固定，级别 warning
    soft_logs = warnings(caplog, "office_budget_soft_limit")
    assert len(soft_logs) == 1
    assert soft_logs[0].levelno == logging.WARNING
    assert not warnings(caplog, "office_budget_hard_stop")


# --------------------------------------------------------------------------- #
# UT-M7-1b：幂等（重复跨越软阈值不重复注入 / 不重复上报）
# --------------------------------------------------------------------------- #


async def test_ut_m7_1b_soft_limit_is_idempotent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """UT-M7-1b：软阈值连续 2 轮都处于超线状态 → 仍只注入一次、只发一条 status 行。"""
    # 预算 20000 / 软比例 0.6 → 软阈值 12000 = 第 12 次调用后跨过；硬阈值 20000 不会触及
    config = make_config(OFFICE_LLM_TOKEN_BUDGET=20000, OFFICE_LLM_BUDGET_SOFT_RATIO=0.6)
    llm = RecordingLLM(tool_rounds=12, final="最终总结：完成。")
    registry, _tool = _registry_with(tmp_path)
    steps, on_progress = collect_progress()
    rows, on_trace = collect_trace()
    runner = ToolLoopRunner(
        config, llm, registry, on_progress=on_progress, on_trace=on_trace
    )

    with caplog.at_level(logging.WARNING):
        outcome = await runner.run("task-m7-1b", "查资料", asyncio.Event())

    assert outcome.stopped_reason == "final_answer"
    # 第 8/9/10 轮都在软阈值之上，但只注入一次
    assert wrap_up_injections(llm) == 1
    assert len(soft_rows(rows)) == 1
    assert len(warnings(caplog, "office_budget_soft_limit")) == 1


# --------------------------------------------------------------------------- #
# UT-M7-2：硬阈值（100%）→ 硬停、不假装成功、上报明确原因
# --------------------------------------------------------------------------- #


async def test_ut_m7_2_hard_stop_reports_reason(tmp_path: Path) -> None:
    """UT-M7-2：达 100% → 停循环（不再调 LLM）、stopped_reason=token_budget、发硬停 status 行。"""
    config = make_config()
    llm = RecordingLLM(tool_rounds=30)  # 第 10 轮（10000 tokens）即应硬停
    registry, tool = _registry_with(tmp_path)
    steps, on_progress = collect_progress()
    rows, on_trace = collect_trace()
    runner = ToolLoopRunner(
        config, llm, registry, on_progress=on_progress, on_trace=on_trace
    )

    outcome = await runner.run("task-m7-2", "查资料", asyncio.Event())

    assert outcome.stopped_reason == "token_budget"
    assert outcome.rounds == 10
    assert llm.calls == 10  # 硬停后不再调用 LLM（没有收尾轮）
    assert tool.calls == 10
    assert outcome.warnings and "预算" in outcome.warnings[0]
    # 不假装成功：硬停路径不产出「最终答复」总结
    assert not outcome.summary.startswith("最终总结")
    # 软阈值先触发（同一任务两级顺序），硬停 status 行标明原因
    assert len(soft_rows(rows)) == 1
    hard = hard_rows(rows)
    assert len(hard) == 1
    assert hard[0]["budget_percent"] == 100
    assert "预算" in hard[0]["message"]


async def test_ut_m7_2_bridge_reports_hard_stop_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UT-M7-2（桥接层）：硬停 → 上报 budget_hard_exceeded 明确原因，绝不报 completed。"""
    callbacks = BudgetCallbacks()
    bridge = prepare_bridge(tmp_path, monkeypatch, callbacks, ScriptedBudgetLLM(tool_rounds=30))

    await bridge.execute_task(make_dispatch("task-m7-2b", tmp_path / "ws"))

    assert callbacks.calls.get("completed") is None
    failed = [event for event in callbacks.events if event[0] == "failed"]
    assert len(failed) == 1
    reason = failed[0][2]
    assert "budget_hard_exceeded" in reason
    assert "10000" in reason  # 带上预算用量（Spec D5：reason + 当前用量）
    # 硬停事件走既有事件通道，带 reason 与用量
    assert callbacks.budget_calls
    task_id, session_id, event_reason, usage = callbacks.budget_calls[0]
    assert task_id == "task-m7-2b"
    assert session_id == office_session_id("task-m7-2b")
    assert "budget_hard_exceeded" in event_reason
    assert usage["prompt_tokens"] > 0 and usage["budget_percent"] == 100


# --------------------------------------------------------------------------- #
# UT-M7-3：任务结束发 usage 行；上报失败只 warning
# --------------------------------------------------------------------------- #


async def test_ut_m7_3_usage_row_payload_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UT-M7-3：任务结束发一条 kind=usage 轨迹行，四个字段齐全且在结束 status 行之前。"""
    callbacks = BudgetCallbacks()
    bridge = prepare_bridge(tmp_path, monkeypatch, callbacks, ScriptedBudgetLLM(tool_rounds=2))

    await bridge.execute_task(make_dispatch("task-m7-3", tmp_path / "ws"))

    assert callbacks.calls.get("completed") == 1
    rows = all_rows(callbacks)
    usage_rows = [row for row in rows if row["kind"] == "usage"]
    assert len(usage_rows) == 1  # 每任务一条（不按执行次数重复）
    payload = usage_rows[0]["payload"]
    assert set(payload) == {"prompt_tokens", "completion_tokens", "rounds", "budget_percent"}
    # 3 次调用（2 轮工具 + 1 轮最终答复）× 1000 tokens
    assert payload["prompt_tokens"] == 3 * _PROMPT_TOKENS
    assert payload["completion_tokens"] == 3 * _COMPLETION_TOKENS
    assert payload["rounds"] == 3
    assert payload["budget_percent"] == 30
    # usage 行在「任务执行结束」status 行之前（结束行始终是最后一条 status 行）
    assert rows[-1]["kind"] == "status"
    assert rows[-1]["payload"]["message"] == "任务执行结束"
    assert rows[-2]["kind"] == "usage"


async def test_ut_m7_3_usage_report_failure_only_warns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UT-M7-3：轨迹上报抛异常 → 任务照常完成、只记 warning（用量是旁路）。"""
    callbacks = BudgetCallbacks(trace_error=RuntimeError("中枢 500"))
    bridge = prepare_bridge(tmp_path, monkeypatch, callbacks, ScriptedBudgetLLM(tool_rounds=1))

    with caplog.at_level(logging.WARNING):
        await bridge.execute_task(make_dispatch("task-m7-3b", tmp_path / "ws"))

    assert callbacks.calls.get("completed") == 1
    assert "failed" not in callbacks.calls
    assert warnings(caplog, "agent_trace_report_failed")
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


async def test_ut_m7_3_budget_stop_report_failure_only_warns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UT-M7-3：硬停事件上报失败 → 只 warning，任务结局仍是 failed（不假装成功）。"""
    callbacks = BudgetCallbacks(send_error=RuntimeError("WS 已断"))
    bridge = prepare_bridge(tmp_path, monkeypatch, callbacks, ScriptedBudgetLLM(tool_rounds=30))

    with caplog.at_level(logging.WARNING):
        await bridge.execute_task(make_dispatch("task-m7-3c", tmp_path / "ws"))

    assert callbacks.calls.get("completed") is None
    assert callbacks.calls.get("failed") == 1
    assert warnings(caplog, "budget_stop_report_failed")
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


# --------------------------------------------------------------------------- #
# 阈值可由配置覆盖
# --------------------------------------------------------------------------- #


async def test_soft_ratio_is_configurable(tmp_path: Path) -> None:
    """阈值由配置覆盖生效：软比例 0.5 → 第 5 轮（5000 tokens）即注入收尾纪律。"""
    default_config = make_config()
    assert default_config.office_llm_budget_soft_ratio == pytest.approx(0.8)

    config = make_config(OFFICE_LLM_BUDGET_SOFT_RATIO=0.5)
    assert config.office_llm_budget_soft_ratio == pytest.approx(0.5)
    llm = RecordingLLM(tool_rounds=6, final="最终总结：完成。")
    registry, _tool = _registry_with(tmp_path)
    _steps, on_progress = collect_progress()
    rows, on_trace = collect_trace()
    runner = ToolLoopRunner(
        config, llm, registry, on_progress=on_progress, on_trace=on_trace
    )

    outcome = await runner.run("task-m7-cfg", "查资料", asyncio.Event())

    assert outcome.stopped_reason == "final_answer"
    soft = soft_rows(rows)
    assert len(soft) == 1
    assert soft[0]["budget_percent"] == 50
    assert first_injection_round(llm) == 6  # 更低阈值 → 更早收尾（第 5 次调用后注入）


# --------------------------------------------------------------------------- #
# 桥接层辅助：假回调 / 假 LLM / 装配
# --------------------------------------------------------------------------- #


class BudgetCallbacks(BridgeCallbacks):
    """收集上报事件、轨迹批次与预算硬停事件的假回调。"""

    def __init__(
        self,
        trace_error: Exception | None = None,
        send_error: Exception | None = None,
    ) -> None:
        self.events: list[tuple] = []
        self.trace_calls: list[dict[str, Any]] = []
        self.budget_calls: list[tuple[str, str, str, dict[str, Any]]] = []
        self.calls: dict[str, int] = {}
        self._trace_error = trace_error
        self._send_error = send_error

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

    async def on_file_chunk(self, request_id, seq, total, data_b64, done):  # noqa: ANN001, ANN202
        self._count("file_chunk")

    async def on_file_error(self, request_id, reason):  # noqa: ANN001, ANN202
        self._count("file_error")

    async def on_agent_trace(self, task_id, *, node_id, session_id, rows):  # noqa: ANN001, ANN202
        self._count("trace")
        self.trace_calls.append(
            {"task_id": task_id, "node_id": node_id, "session_id": session_id, "rows": rows}
        )
        if self._trace_error is not None:
            raise self._trace_error

    async def _send_event(self, event: str, payload: dict[str, Any]) -> None:
        """模拟 HubClient 的通用事件发送（office 现行硬停上报路径）。"""
        self._count("send_event")
        if self._send_error is not None:
            raise self._send_error
        if event == "agent.task_budget_stop":
            self._count("budget_stop")
            self.budget_calls.append(
                (
                    str(payload.get("task_id")),
                    str(payload.get("session_id")),
                    str(payload.get("reason")),
                    dict(payload.get("usage") or {}),
                )
            )


class ScriptedBudgetLLM:
    """脚本化 LLM（前 N 轮工具调用、随后最终答复），每次调用固定 100 tokens。"""

    model = "fake-model"

    def __init__(self, tool_rounds: int, final: str = "最终总结：已交付。") -> None:
        self.tool_rounds = tool_rounds
        self.final = final
        self.calls = 0

    async def chat(self, messages, tools=None):  # noqa: ANN001, ANN202
        self.calls += 1
        if self.calls <= self.tool_rounds:
            message: dict[str, Any] = {
                "role": "assistant",
                "content": "",
                "tool_calls": [tool_call(f"c{self.calls}", "web_search", {"query": "资料"})],
            }
        elif self.calls == self.tool_rounds + 1:
            message = {"role": "assistant", "content": self.final}
        else:
            message = {"role": "assistant", "content": "完成。"}
        return {
            "message": message,
            "prompt_tokens": _PROMPT_TOKENS,
            "completion_tokens": _COMPLETION_TOKENS,
        }


def make_dispatch(task_id: str, output_dir: Path | str) -> TaskDispatchPayload:
    """最小派发载荷（无 checklist：交付质量闸不生效，专注护栏本身）。"""
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


def all_rows(callbacks: BudgetCallbacks, task_id: str | None = None) -> list[dict[str, Any]]:
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
    callbacks: BudgetCallbacks,
    scripted: ScriptedBudgetLLM,
) -> OfficeBridge:
    """装配桥接：假 LLM + 假工具集 + 已绑定轨迹通道。"""
    bridge = OfficeBridge(make_config(WORKSPACE_ROOT=str(tmp_path)), callbacks)
    bridge.bind_agent_trace(callbacks.on_agent_trace)
    monkeypatch.setattr(bridge_mod, "default_registry", make_registry)

    class FakeLLMClient:
        def __init__(self, **kwargs: Any) -> None:
            self.model = kwargs.get("model", "fake-model")

        async def chat(self, messages, tools=None):  # noqa: ANN001, ANN202
            return await scripted.chat(messages, tools)

    monkeypatch.setattr(bridge_mod, "LLMClient", FakeLLMClient)
    return bridge


# --------------------------------------------------------------------------- #
# 跨执行累计：回灌轮用量计入任务累计（软阈值在回灌轮注入一次即幂等）
# --------------------------------------------------------------------------- #


async def test_usage_accumulates_across_repair_rounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回灌轮用量计入累计：usage 行是任务级总量，软阈值在回灌轮只注入一次。"""
    callbacks = BudgetCallbacks()
    bridge = OfficeBridge(
        make_config(
            WORKSPACE_ROOT=str(tmp_path),
            OFFICE_LLM_TOKEN_BUDGET=10000,
            OFFICE_LLM_BUDGET_SOFT_RATIO=0.1,
        ),
        callbacks,
    )
    bridge.bind_agent_trace(callbacks.on_agent_trace)
    monkeypatch.setattr(bridge_mod, "default_registry", make_registry)
    scripted = ScriptedBudgetLLM(tool_rounds=1, final="已交付初稿。")
    wrapper = _FakeLLMFactory(scripted)
    monkeypatch.setattr(bridge_mod, "LLMClient", wrapper)

    # checklist 必过项永不满足 → 触发回灌轮（首轮 + 5 轮回灌 = 6 次执行）
    dispatch = TaskDispatchPayload.model_validate(
        {
            "task_id": "task-m7-acc",
            "task_name": "写调研报告",
            "description": "调研某主题并产出 docx",
            "specification": {
                "spec": "报告需含结论章节",
                "tasks": "",
                "checklist": "C1 交付物必须包含「结论」章节（must_pass）",
            },
            "executor_model": {
                "source": "hub",
                "model": "deepseek/deepseek-chat",
                "provider": "deepseek",
                "api_key": "sk-test-123",
                "base_url": "https://api.deepseek.com",
            },
            "output_dir": str(tmp_path / "ws"),
        }
    )

    await bridge.execute_task(dispatch)

    rows = all_rows(callbacks)
    usage_rows = [row for row in rows if row["kind"] == "usage"]
    assert len(usage_rows) == 1
    payload = usage_rows[0]["payload"]
    # 每次执行 2 次调用（1 轮工具 + 1 轮最终答复），共 6 次执行
    assert payload["prompt_tokens"] == 12 * _PROMPT_TOKENS
    assert payload["completion_tokens"] == 12 * _COMPLETION_TOKENS
    assert payload["rounds"] == 2  # 单次执行的轮次上限语义（取最大，不累加）
    assert payload["budget_percent"] == 100
    # 软阈值在回灌轮跨过：只注入一次、只上报一条 status 行
    assert len(soft_rows(rows)) == 1


class _FakeLLMFactory:
    """把桥接层构造的 LLMClient 换成给定脚本替身。"""

    def __init__(self, scripted: Any) -> None:
        self._scripted = scripted

    def __call__(self, **kwargs: Any) -> Any:
        scripted = self._scripted

        class _Client:
            model = "fake-model"

            async def chat(self, messages, tools=None):  # noqa: ANN001, ANN202
                return await scripted.chat(messages, tools)

        return _Client()
