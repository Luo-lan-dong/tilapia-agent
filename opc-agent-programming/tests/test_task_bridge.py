"""TaskBridge 单元测试：基于 fake client/store/workspace，无真实网络。

覆盖 execute_task 的 drain → 正常事件 → 完成判定路径、失败路径、
断线重连、interrupt、handle_tilacode_crash 与权限兜底泵，以及任务迭代链
（project_key 维度）与 plan 方案上报（见 ADR-012 2026-08-15 增补决策）。

注：本文件超过测试文件 400 行建议上限（见 coding-standards.md 4.3），
因任务桥接各场景共享 fake 组件与事件构造工具，拆分反而重复脚手架，
故按豁免场景保留并在此说明。
"""

import asyncio
import base64
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import AgentConfig
from app.executor import permission_policy, verify_runner, verify_tamper
from app.executor.session_store import SessionStore, TaskMappingStore
from app.executor.task_bridge import _FREE_MODEL_DESC, BridgeCallbacks, TaskBridge
from app.executor.workspace import WorkspaceManager
from app.models.schemas import TaskAttachmentInfo, TaskDispatchPayload, TaskSpecification


@pytest.fixture(autouse=True)
def _passing_verify_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """让本文件既有用例聚焦原意图：验收闸替换为「总是放行」（ADR-061 M4）。

    闸门自身行为（必过层红/无法验证 → 阻断、自修 ≤5 轮、报告落痕）在
    `test_verify_runner.py` 专门覆盖；不在此文件用真实命令派生判据。
    """
    monkeypatch.setattr(
        verify_runner,
        "load_plan",
        lambda workspace_dir, doc_dir: ({"checks": []}, "verify_json"),
    )

    async def _passing_run(workspace_dir, plan, source):  # noqa: ANN001, ARG001
        return verify_runner.VerifyReport(status="passed", source=source, checks=[])

    monkeypatch.setattr(verify_runner, "run_plan", _passing_run)


async def test_steer_sends_prompt_and_delivery_ack(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-M5a-8（桥接级，ADR-061 M5a-steer）：按 `ses_task_{项目键}` 送话 + 用已知进度回送达确认。

    回归对象：此前只有**服务级**用例，桥接层的两个约定（会话 ID 归属、确认不带 0 进度）
    仅靠与已测 `interrupt` 逐行同构保证。
    """
    client = FakeTilacodeClient()
    bridge, calls = _gate_bridge(config, workspace, tmp_path, client)
    session_id = "ses_task_t1"
    bridge._current_tasks["t1"] = SimpleNamespace(task_id="t1", base_task_id=None)
    bridge._session_directories[session_id] = tmp_path
    bridge._last_progress_value["t1"] = 42

    delivered = await bridge.steer("t1", "请先跑测试再改代码")

    assert delivered is True
    # 送话：会话归属 + 工作目录
    assert client.prompts == [(session_id, "请先跑测试再改代码")]
    # 不打断任务（与 interrupt 的本质区别）
    assert client.interrupts == []
    # 送达确认：进度用**已知值**（不回退 0），stage/tool/detail 传 None 由中枢保持原值
    task_id, progress, todos, stage, message, tool, detail = calls["progress"][0]
    assert (task_id, progress, todos, stage, tool, detail) == ("t1", 42, None, None, None, None)
    assert "已收到中枢指令" in (message or "")
    assert "请先跑测试再改代码" in (message or "")
    # 不在本节点执行的任务 → 明确返回 False，不抛错、不送话
    assert await bridge.steer("missing-task", "无关内容") is False
    assert len(client.prompts) == 1


async def test_steer_skips_ack_when_progress_unknown(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-M5a-8b：不知道进度时**不发**确认（避免把中枢进度条打回 0），送话仍完成。"""
    client = FakeTilacodeClient()
    bridge, calls = _gate_bridge(config, workspace, tmp_path, client)
    bridge._current_tasks["t1"] = SimpleNamespace(task_id="t1", base_task_id=None)
    bridge._session_directories["ses_task_t1"] = tmp_path

    assert await bridge.steer("t1", "补充一句要求") is True

    assert len(client.prompts) == 1
    assert calls["progress"] == []


async def test_steer_uses_chain_root_session(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-M5a-8c：任务链上发言必须落到**根任务会话**（与 interrupt 同一约定）。"""
    client = FakeTilacodeClient()
    bridge, _calls = _gate_bridge(config, workspace, tmp_path, client)
    bridge._current_tasks["child"] = SimpleNamespace(task_id="child", base_task_id="root")
    bridge._session_directories["ses_task_root"] = tmp_path

    assert await bridge.steer("child", "链上发言") is True

    assert client.prompts == [("ses_task_root", "链上发言")]


def make_event(seq: int, event_type: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """构造带 durable.seq 的 tilacode SSE 事件。"""
    return {
        "id": f"evt_{seq}",
        "type": event_type,
        "data": data or {},
        "durable": {"aggregateID": "ses_task_t1", "seq": seq, "version": 1},
    }


class Hold:
    """事件流中的挂起点：release 前阻塞消费循环，用于控制时序。"""

    def __init__(self) -> None:
        self.event = asyncio.Event()

    def release(self) -> None:
        self.event.set()


class FakeTilacodeClient:
    """测试用 tilacode 客户端：按订阅次序回放预置事件流。"""

    def __init__(
        self,
        streams: list[list[Any]] | None = None,
        active: bool = False,
        session_agent: str | None = None,
        instance_events: list[Any] | None = None,
        history: list[dict[str, Any]] | None = None,
        context_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        self.created: list[dict[str, Any]] = []
        self.prompts: list[tuple[str, str]] = []
        self.switched: list[tuple[str, str]] = []
        self.interrupts: list[str] = []
        self.config_invalidations: list[str] = []
        #: 派发前预检（ADR-061 C1）：每次 GET /api/provider 的目录
        self.preflights: list[str] = []
        #: 预检返回的 provider → 模型清单（测试按需注入；默认空 = 目录为空）
        self.provider_snapshot_data: dict[str, list[str]] = {}
        self.integration_keys: list[tuple[str, str]] = []
        self.integration_key_kwargs: list[dict[str, Any]] = []
        self.permission_replies: list[tuple[str, str, str]] = []
        #: 每次回复携带的拒绝原因（ADR-061 M2b：reject 时必须可读，once 时为 None）
        self.permission_reply_messages: list[str | None] = []
        self.stream_calls: list[int | None] = []
        # 每个会话各自计数订阅次序：同一会话先后创建多个消费者（如总结轮）时，
        # 不能按全局调用序号取流，否则会串到其他会话的流（ADR-055 测试修复）。
        self._session_stream_index: dict[str, int] = {}
        self.instance_stream_directories: list[str | None] = []
        self.prompt_sent = asyncio.Event()
        self._streams = streams or []
        self._active = active
        self._session_agent = session_agent
        self._instance_events = instance_events or []
        self._history = history or []
        self._context_messages = context_messages or []

    async def close(self) -> None:
        """空实现：fake 无底层连接。"""

    async def create_session(
        self,
        *,
        session_id: str,
        agent: str,
        directory: str,
        model: dict[str, str] | None = None,
    ) -> dict:
        self.created.append(
            {"session_id": session_id, "agent": agent, "directory": directory, "model": model}
        )
        return {"data": {"id": session_id, "agent": self._session_agent or agent}}

    async def switch_agent(self, session_id: str, agent: str, **kwargs: Any) -> None:
        self.switched.append((session_id, agent))

    async def ensure_integration_key(
        self, integration_id: str, api_key: str, **kwargs: Any
    ) -> None:
        self.integration_keys.append((integration_id, api_key))
        self.integration_key_kwargs.append(kwargs)

    async def send_prompt(self, session_id: str, text: str, **kwargs: Any) -> dict:
        self.prompts.append((session_id, text))
        self.prompt_sent.set()
        return {"data": {"sessionID": session_id}}

    async def interrupt(self, session_id: str, **kwargs: Any) -> None:
        self.interrupts.append(session_id)

    async def invalidate_config(self, *, directory: str, **kwargs: Any) -> None:
        """PATCH /config 触发目录实例失效（ADR-055 D3 修订）。"""
        self.config_invalidations.append(directory)

    async def provider_snapshot(self, *, directory: str, **kwargs: Any) -> dict[str, list[str]]:
        """派发前预检端点（ADR-061 C1）：GET /api/provider。"""
        self.preflights.append(directory)
        return dict(self.provider_snapshot_data)

    async def is_session_active(self, session_id: str, **kwargs: Any) -> bool:
        return self._active

    async def get_history(self, session_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        """durable 事件分页回退路径（ADR-055 P0-5）。"""
        return self._history

    async def get_context(self, session_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        """会话上下文消息数组（ADR-055 P0-5：优先取数端点）。"""
        return self._context_messages

    async def permission_reply(
        self,
        session_id: str,
        request_id: str,
        reply: str = "once",
        *,
        message: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.permission_replies.append((session_id, request_id, reply))
        self.permission_reply_messages.append(message)

    async def stream_session_events(self, session_id: str, after: int | None = None):
        self.stream_calls.append(after)
        index = self._session_stream_index.get(session_id, 0)
        self._session_stream_index[session_id] = index + 1
        events = self._streams[index] if index < len(self._streams) else []
        for item in events:
            if isinstance(item, Hold):
                await item.event.wait()
            else:
                yield item

    async def stream_instance_events(self, directory: str | None = None):
        self.instance_stream_directories.append(directory)
        for item in self._instance_events:
            if isinstance(item, Hold):
                await item.event.wait()
            else:
                yield item


def make_callbacks() -> tuple[BridgeCallbacks, dict[str, list[tuple]]]:
    """构造记录型回调集合（含 ADR-028 产物回传回调）。"""
    calls: dict[str, list[tuple]] = {
        "progress": [],
        "task_started": [],
        "completed": [],
        "failed": [],
        "file_tree": [],
        "file_chunk": [],
        "file_error": [],
        #: 验收阻断回调（ADR-061 M4）
        "verify_blocked": [],
        #: 节点轨迹行上报（ADR-063 M1）
        "agent_trace": [],
    }

    async def on_progress(
        task_id: str,
        progress: int,
        todos: list[dict] | None,
        *,
        stage: str | None = None,
        message: str | None = None,
        tool: str | None = None,
        detail: dict | None = None,
    ) -> None:
        calls["progress"].append((task_id, progress, todos, stage, message, tool, detail))

    async def on_task_started(task_id: str, executor_model_actual: str) -> None:
        calls["task_started"].append((task_id, executor_model_actual))

    async def on_completed(
        task_id: str,
        session_id: str,
        plan_content: str | None,
        change_summary: str | None,
        project_context: str | None = None,
        *,
        verify_report: dict | None = None,
    ) -> None:
        calls["completed"].append((task_id, session_id, plan_content, change_summary))

    async def on_failed(task_id: str, reason: str) -> None:
        calls["failed"].append((task_id, reason))

    async def on_file_tree(task_id: str, files: list[dict], truncated: bool) -> None:
        calls["file_tree"].append((task_id, files, truncated))

    async def on_file_chunk(request_id: str, seq: int, total: int, data: str, done: bool) -> None:
        calls["file_chunk"].append((request_id, seq, total, data, done))

    async def on_file_error(request_id: str, message: str) -> None:
        calls["file_error"].append((request_id, message))

    async def on_verify_blocked(task_id: str, project_key: str, report: dict) -> None:
        calls["verify_blocked"].append((task_id, project_key, report))

    async def on_agent_trace(
        task_id: str,
        *,
        node_id: str | None = None,
        session_id: str | None = None,
        rows: list[dict] | None = None,
    ) -> None:
        """记录轨迹行上报（ADR-063 M1；与 HubClient.report_agent_trace 同签名）。"""
        calls["agent_trace"].append((task_id, node_id, session_id, rows))

    callbacks = BridgeCallbacks(
        on_progress=on_progress,
        on_task_started=on_task_started,
        on_completed=on_completed,
        on_failed=on_failed,
        on_verify_blocked=on_verify_blocked,
        on_agent_trace=on_agent_trace,
        on_file_tree=on_file_tree,
        on_file_chunk=on_file_chunk,
        on_file_error=on_file_error,
    )
    return callbacks, calls


@pytest.fixture
def no_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """跳过 build 完成后的总结轮（不关心 change_summary 的测试使用）。"""
    monkeypatch.setattr(TaskBridge, "_generate_change_summary", AsyncMock(return_value=None))


@pytest.fixture
def config() -> AgentConfig:
    """测试配置：静默判定时长压缩到 0.1 秒加速完成判定。"""
    return AgentConfig(
        agent_registration_token="test-token",
        tilacode_step_idle_timeout_seconds=0.1,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceManager:
    """指向临时目录的工作目录管理器（用户级配置目录同样隔离到 tmp，ADR-055 D3）。"""
    return WorkspaceManager(str(tmp_path / "ws"), config_dir=tmp_path / "cfg")


def make_payload(task_id: str = "t1") -> TaskDispatchPayload:
    """构造标准任务派发消息。"""
    return TaskDispatchPayload(
        task_id=task_id,
        task_name="实现登录",
        description="实现登录功能",
        mode="build",
        specification=TaskSpecification(spec="# spec", tasks=None, checklist="# cl"),
    )


# 捕获真实 asyncio.sleep（模块导入时未被 monkeypatch），供 _no_sleep 让出事件循环
_real_sleep = asyncio.sleep


async def _no_sleep(_delay: float) -> None:
    """跳过断线重连退避等待，改用极短的**真实**等待让出事件循环。

    ADR-028 后 execute_task 会启动周期文件清单上报任务；ADR-055 又新增了
    无进度看门狗与静默期进度心跳——三者都通过 ``asyncio.sleep`` 周期性轮询。
    若把 sleep 短路成「零延迟 + 让出」，这些周期任务会持续抢占就绪队列，
    把真正干活的 SSE 消费任务饿死（表现为测试挂起）。

    因此改为一毫秒的真实等待：总耗时极小，但保证让出后所有就绪任务都能推进。
    """
    await _real_sleep(0.001)


async def test_execute_task_completes_after_idle_and_drains_history(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
    no_summary: None,
) -> None:
    """drain 排水历史事件；正常事件限频上报；静默复核后完成并回调。"""
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    first = [
        make_event(1, "session.next.tool.called", {"tool": "bash"}),  # 历史重放，应被排水
        make_event(2, "session.next.prompt.admitted"),
        make_event(3, "session.next.step.started", {"agent": "build"}),
        make_event(4, "session.next.tool.called", {"tool": "edit", "input": {"filePath": "a.py"}}),
        make_event(5, "session.next.tool.failed", {"tool": "grep"}),  # 进度不变：不上报
        make_event(6, "session.next.step.ended", {"tokens": {"input": 10, "output": 2}}),
    ]
    hold_open = Hold()  # 重连后保持连接无事件，等待静默超时
    client = FakeTilacodeClient(streams=[first, [hold_open]], active=False)
    callbacks, calls = make_callbacks()
    store = SessionStore(tmp_path / "sessions.json")
    bridge = TaskBridge(config, client, store, workspace, callbacks)

    payload = make_payload()
    await bridge.execute_task(payload)

    # 上报项目键；build 模式无 plan 产物（本测试跳过总结轮）
    assert calls["completed"] == [("t1", "t1", None, None)]
    assert calls["failed"] == []
    # 历史事件（seq=1）不产生进度；首个上报来自 step.started 的 20；
    # tool.failed 进度不变被限频跳过；step.ended 再推进 2
    assert [(item[0], item[1], item[2]) for item in calls["progress"]] == [
        ("t1", 20, None),
        ("t1", 22, None),
        ("t1", 24, None),
    ]
    # ADR-055 P1-9：进度事件携带执行阶段与当前动作
    assert [item[3] for item in calls["progress"]] == ["coding", "editing", "editing"]
    assert calls["progress"][1][4] == "正在处理 a.py"
    assert calls["progress"][1][5] == "edit"
    # step.ended 的可观测明细透传
    assert calls["progress"][2][6] == {
        "tokens": {
            "input": 10,
            "output": 2,
            "reasoning": None,
            "cache_read": None,
            "cache_write": None,
        }
    }
    # ADR-055 P1-6：流结束且处于静默态 → 直接复核 active 判完成，**不重连**
    # （旧实现会先重连一次再靠 30s 静默超时拆流，既慢又多一次订阅）
    assert client.stream_calls == [None]
    # prompt 组装：描述 + 实际写入的文档（spec/checklist，无 tasks；ADR-037 版本目录）
    prompt = client.prompts[0][1]
    assert "实现登录功能" in prompt
    assert "specs/实现登录-t1/spec.md" in prompt
    assert "specs/实现登录-t1/checklist.md" in prompt
    assert "tasks.md" not in prompt
    # 规格文档落盘与登记簿状态
    task_dir = tmp_path / "ws" / "t1"
    assert (task_dir / "specs" / "实现登录-t1" / "spec.md").exists()
    record = await store.get("t1")
    assert record is not None and record.status == "completed"


async def test_execute_task_fails_on_session_error(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """session.error 事件应触发失败回调并提取错误摘要。"""
    first = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.started"),
        make_event(
            3,
            "session.error",
            {"error": {"name": "ProviderError", "message": "LLM 上游 401"}},
        ),
    ]
    client = FakeTilacodeClient(streams=[first])
    callbacks, calls = make_callbacks()
    store = SessionStore(tmp_path / "sessions.json")
    bridge = TaskBridge(config, client, store, workspace, callbacks)

    await bridge.execute_task(make_payload())

    assert calls["completed"] == []
    assert len(calls["failed"]) == 1
    task_id, reason = calls["failed"][0]
    assert task_id == "t1" and "LLM 上游 401" in reason
    record = await store.get("t1")
    assert record is not None and record.status == "failed"


async def test_execute_task_fails_on_step_failed(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """session.next.step.failed 事件应触发失败回调。"""
    first = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.failed", {"error": {"message": "bash 工具执行失败"}}),
    ]
    client = FakeTilacodeClient(streams=[first])
    callbacks, calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    await bridge.execute_task(make_payload())

    task_id, reason = calls["failed"][0]
    assert task_id == "t1" and "bash 工具执行失败" in reason


async def test_execute_task_switches_agent_when_reused(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    no_summary: None,
) -> None:
    """复用 Session 返回 agent 与请求 mode 不一致时应调用 switch_agent。"""
    first = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.ended"),
        Hold(),  # 挂起等待静默超时
    ]
    client = FakeTilacodeClient(streams=[first], active=False, session_agent="plan")
    callbacks, _calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    await bridge.execute_task(make_payload())  # mode 默认 build

    assert client.switched == [("ses_task_t1", "build")]


async def test_execute_task_plan_mode_prompt_hint(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """plan 模式 prompt 应包含规划目录提示且不改业务代码。"""
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    prompt_hold = Hold()
    first = [make_event(1, "session.next.prompt.admitted"), prompt_hold]
    second = [make_event(2, "session.next.step.ended"), Hold()]
    client = FakeTilacodeClient(streams=[first, second], active=False)
    callbacks, _calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    payload = make_payload()
    payload.mode = "plan"
    task = asyncio.create_task(bridge.execute_task(payload))
    await client.prompt_sent.wait()
    prompt = client.prompts[0][1]
    assert ".tilacode/plans/" in prompt
    assert client.created[0]["agent"] == "plan"
    prompt_hold.release()  # 放行流结束，让重连流走 step.ended → 静默 → 完成
    await task
    # plan 模式但未产出 plans 文档：plan_content 为 None，且不走总结轮
    assert _calls["completed"] == [("t1", "t1", None, None)]


async def test_handle_tilacode_crash_fails_current_task(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """执行中收到 tilacode 崩溃回调应按 tilacode_crashed 失败结束。"""
    hold = Hold()
    first = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.started"),
        hold,
        make_event(3, "session.next.step.ended"),
    ]
    client = FakeTilacodeClient(streams=[first], active=True)
    callbacks, calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    task = asyncio.create_task(bridge.execute_task(make_payload()))
    await client.prompt_sent.wait()
    await bridge.handle_tilacode_crash("tilacode 进程异常退出（returncode=1）")
    hold.release()  # 模拟进程退出导致的断流
    await task

    task_id, reason = calls["failed"][0]
    assert task_id == "t1"
    assert reason == "tilacode_crashed: tilacode 进程异常退出（returncode=1）"


async def test_interrupt_fails_task_with_interrupted_reason(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """interrupt 应调用 tilacode 中断 API 并按 interrupted 失败上报。"""
    hold = Hold()
    first = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.started"),
        hold,
        make_event(3, "session.next.step.ended"),
    ]
    client = FakeTilacodeClient(streams=[first])
    callbacks, calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    task = asyncio.create_task(bridge.execute_task(make_payload()))
    await client.prompt_sent.wait()
    await bridge.interrupt("t1")
    hold.release()  # 中断后流继续产出 step.ended
    await task

    assert client.interrupts == ["ses_task_t1"]
    assert calls["failed"] == [("t1", "interrupted")]


async def test_task_event_pump_replies_once_when_allowlisted(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-M2b-5（ADR-061 M2b）：命中白名单 → 回 ``once``，不带拒绝原因。

    回归对象：此前实现**无条件**回 ``once``（`tilacode_client.permission_reply`
    默认实参），等于把 tilacode 的「工作区外 deny / ``.env`` 拒绝」等护栏全部绕过。
    """
    workspace.ensure_tilacode_config(
        tmp_path,
        policy=permission_policy.normalize_policy(
            {"mode": "unattended", "allow": [{"action": "bash", "resource": "git *"}]}
        ),
    )
    permission_event = make_event(
        1,
        "permission.v2.asked",
        {
            "id": "per_1",
            "sessionID": "ses_task_t1",
            "action": "bash",
            "resources": ["git status"],
        },
    )
    hold_open = Hold()
    client = FakeTilacodeClient(instance_events=[permission_event, hold_open])
    callbacks, _calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )
    # 直接驱动事件泵（不经过 _prepare_session），需手动登记会话目录——
    # 这同时覆盖「内存无策略记录时回读工作区配置」的兜底路径（Q2）
    bridge._session_directories["ses_task_t1"] = tmp_path

    pump = asyncio.create_task(bridge._task_event_pump("t1", tmp_path))
    try:
        for _ in range(100):
            if client.permission_replies:
                break
            await asyncio.sleep(0.01)
        assert client.permission_replies == [("ses_task_t1", "per_1", "once")]
        assert client.permission_reply_messages == [None]
        assert client.instance_stream_directories == [str(tmp_path)]
    finally:
        hold_open.release()
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        await bridge.shutdown()


async def test_task_event_pump_rejects_when_not_allowlisted(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-M2b-6（ADR-061 M2b，Q3）：未命中白名单 → 回 ``reject`` + 可读原因。"""
    workspace.ensure_tilacode_config(
        tmp_path,
        policy=permission_policy.normalize_policy(
            {"mode": "unattended", "allow": [{"action": "read", "resource": "/usr/include/**"}]}
        ),
    )
    permission_event = make_event(
        1,
        "permission.v2.asked",
        {
            "id": "per_2",
            "sessionID": "ses_task_t1",
            "action": "bash",
            "resources": ["rm -rf /"],
        },
    )
    hold_open = Hold()
    client = FakeTilacodeClient(instance_events=[permission_event, hold_open])
    callbacks, _calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )
    bridge._session_directories["ses_task_t1"] = tmp_path

    pump = asyncio.create_task(bridge._task_event_pump("t1", tmp_path))
    try:
        for _ in range(100):
            if client.permission_replies:
                break
            await asyncio.sleep(0.01)
        assert client.permission_replies == [("ses_task_t1", "per_2", "reject")]
        reason = client.permission_reply_messages[0]
        assert reason is not None and "不在中枢下发的权限白名单内" in reason
    finally:
        hold_open.release()
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        await bridge.shutdown()


async def test_task_event_pump_rejects_when_action_missing(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-M2b-7（ADR-061 M2b，fail-closed）：``action``/``resources`` 缺失也拒绝。

    宁可多拒不可少拒：无法评估的请求不得默认放行。
    """
    workspace.ensure_tilacode_config(
        tmp_path,
        policy=permission_policy.normalize_policy(
            {"mode": "unattended", "allow": [{"action": "*", "resource": "/opt/**"}]}
        ),
    )
    permission_event = make_event(
        1, "permission.v2.asked", {"id": "per_3", "sessionID": "ses_task_t1"}
    )
    hold_open = Hold()
    client = FakeTilacodeClient(instance_events=[permission_event, hold_open])
    callbacks, _calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )
    bridge._session_directories["ses_task_t1"] = tmp_path

    pump = asyncio.create_task(bridge._task_event_pump("t1", tmp_path))
    try:
        for _ in range(100):
            if client.permission_replies:
                break
            await asyncio.sleep(0.01)
        assert client.permission_replies == [("ses_task_t1", "per_3", "reject")]
        assert client.permission_reply_messages[0] is not None
    finally:
        hold_open.release()
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        await bridge.shutdown()


async def test_task_event_pump_forwards_todo_updated_to_consumer(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """实例级 SSE 收到 todo.updated 时，应转发给 sessionID 匹配的当前 consumer。

    事件 payload 同时覆盖 properties（旧版兼容）与 data（线上实测格式）两种载体。
    """
    todo_event = {
        "id": "evt_todo_1",
        "type": "todo.updated",
        "data": {
            "sessionID": "ses_task_t1",
            "todos": [
                {"content": "验证 smali 文件修改", "status": "completed"},
                {"content": "验证 APK 签名", "status": "pending"},
            ],
        },
    }
    hold_open = Hold()
    client = FakeTilacodeClient(instance_events=[todo_event, hold_open])
    callbacks, _calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    consumer = AsyncMock()
    consumer.session_id = "ses_task_t1"
    consumer.task_id = "t1"
    bridge._consumers["t1"] = consumer

    pump = asyncio.create_task(bridge._task_event_pump("t1", tmp_path))
    try:
        for _ in range(100):
            if consumer.handle_todo_event.called:
                break
            await asyncio.sleep(0.01)
        consumer.handle_todo_event.assert_awaited_once()
        args = consumer.handle_todo_event.await_args
        assert args is not None
        data = args.args[0]
        assert data["sessionID"] == "ses_task_t1"
        assert len(data["todos"]) == 2
        assert data["todos"][0]["content"] == "验证 smali 文件修改"
    finally:
        hold_open.release()
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        await bridge.shutdown()


async def test_task_event_pump_ignores_todo_updated_for_other_session(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """实例级 todo.updated 的 sessionID 与当前 consumer 不匹配时应忽略。"""
    todo_event = {
        "id": "evt_todo_1",
        "type": "todo.updated",
        "data": {
            "sessionID": "ses_task_other",
            "todos": [{"content": "其他 session 的待办", "status": "pending"}],
        },
    }
    hold_open = Hold()
    client = FakeTilacodeClient(instance_events=[todo_event, hold_open])
    callbacks, _calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    consumer = AsyncMock()
    consumer.session_id = "ses_task_t1"
    consumer.task_id = "t1"
    bridge._consumers["t1"] = consumer

    pump = asyncio.create_task(bridge._task_event_pump("t1", tmp_path))
    try:
        for _ in range(50):
            if consumer.handle_todo_event.called:
                break
            await asyncio.sleep(0.01)
        consumer.handle_todo_event.assert_not_awaited()
    finally:
        hold_open.release()
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        await bridge.shutdown()


async def test_shutdown_closes_client(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """shutdown 幂等：关闭客户端连接，重复调用不报错。"""
    client = FakeTilacodeClient(instance_events=[Hold()])
    callbacks, _calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    await bridge.shutdown()
    await bridge.shutdown()  # 幂等


async def test_execute_task_reuses_base_task_project_key(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
    no_summary: None,
) -> None:
    """base_task_id 存在时工作目录/Session/登记簿均以根任务 key 归属。"""
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    first = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.started"),
        make_event(3, "session.next.step.ended"),
    ]
    hold_open = Hold()
    client = FakeTilacodeClient(streams=[first, [hold_open]], active=False)
    callbacks, calls = make_callbacks()
    store = SessionStore(tmp_path / "sessions.json")
    bridge = TaskBridge(config, client, store, workspace, callbacks)

    payload = make_payload(task_id="t9")
    payload.base_task_id = "root-1"
    payload.change_brief = "优化登录接口性能"
    await bridge.execute_task(payload)

    # Session 与工作目录均挂在根任务 key 下，不为本轮任务另建目录
    assert client.created[0]["session_id"] == "ses_task_root-1"
    assert client.created[0]["directory"] == str(tmp_path / "ws" / "root-1")
    assert (tmp_path / "ws" / "root-1").is_dir()
    assert not (tmp_path / "ws" / "t9").exists()
    # 登记簿记录 project_key 而非本轮 task_id；上报中枢仍用本轮 task_id
    record = await store.get("root-1")
    assert record is not None and record.status == "completed"
    assert await store.get("t9") is None
    assert calls["completed"] == [("t9", "root-1", None, None)]  # 上报链根项目键
    # prompt 走任务链迭代分支文案
    prompt = client.prompts[0][1]
    assert "本轮任务：优化登录接口性能" in prompt
    assert "不要重复开发已有功能" in prompt


async def test_build_prompt_iteration_scenarios(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """prompt 组装四分支：brief+文档 / brief+无文档 / 无 brief / 无 base_task_id。"""
    callbacks, _calls = make_callbacks()
    bridge = TaskBridge(
        config, FakeTilacodeClient(), SessionStore(tmp_path / "s.json"), workspace, callbacks
    )

    # 分支 a：base 存在、change_brief 非空且本轮带文档 → 指向本轮版本目录（ADR-037）
    payload_brief = make_payload()
    payload_brief.base_task_id = "root-1"
    payload_brief.change_brief = "把登录改为 JWT"
    prompt = bridge._build_prompt(payload_brief, ["specs/实现登录-t1/spec.md"], tmp_path)
    assert "本轮任务：把登录改为 JWT" in prompt
    assert "本轮需求文档已更新至 specs/实现登录-t1/spec.md" in prompt
    assert "历史版本保留在 specs/ 其他目录" in prompt
    assert "不要重复开发已有功能" in prompt
    assert "工作目录下有需求文档" not in prompt

    # 分支 b：base 存在、change_brief 非空但本轮未带文档 → 不再声称"已更新为最新版"
    prompt = bridge._build_prompt(payload_brief, [], tmp_path)
    assert "本轮任务：把登录改为 JWT" in prompt
    assert "本轮未重新生成需求文档" in prompt
    assert "历史文档见 specs/ 各目录" in prompt
    assert "已更新至" not in prompt
    assert "不要重复开发已有功能" in prompt

    # 分支 c：base 存在但 change_brief 为空 → 继续完善文案（指向 specs/）
    payload_no_brief = make_payload()
    payload_no_brief.base_task_id = "root-1"
    prompt = bridge._build_prompt(payload_no_brief, ["specs/spec.md"], tmp_path)
    assert "历史需求文档见 specs/ 目录" in prompt
    assert "工作目录下有需求文档" not in prompt

    # 分支 d：无 base_task_id → 保持原有文案
    prompt = bridge._build_prompt(make_payload(), ["specs/spec.md", "specs/checklist.md"], tmp_path)
    assert "工作目录下有需求文档（specs/spec.md、specs/checklist.md）" in prompt
    assert "该项目代码已存在" not in prompt


async def test_plan_mode_completion_reports_plan_content(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """plan 模式完成时应读取 plans 产物拼接后随完成回调上报。"""
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    plans_dir = tmp_path / "ws" / "t1" / ".tilacode" / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "b-database.md").write_text("# 数据库方案", encoding="utf-8")
    (plans_dir / "a-api.md").write_text("# API 方案", encoding="utf-8")

    first = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.ended"),
    ]
    hold_open = Hold()
    client = FakeTilacodeClient(streams=[first, [hold_open]], active=False)
    callbacks, calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    payload = make_payload()
    payload.mode = "plan"
    await bridge.execute_task(payload)

    assert len(calls["completed"]) == 1
    task_id, session_id, plan_content, change_summary = calls["completed"][0]
    assert task_id == "t1" and session_id == "t1"
    assert plan_content is not None
    assert change_summary is None  # plan 模式不生成修改说明
    # 两个文件名标题均在，且按文件名排序（a-api 在前），多文件以分隔线相连
    assert "## a-api.md" in plan_content and "## b-database.md" in plan_content
    assert plan_content.index("## a-api.md") < plan_content.index("## b-database.md")
    assert "# API 方案" in plan_content and "# 数据库方案" in plan_content
    assert "\n\n---\n\n" in plan_content


async def test_build_mode_completion_generates_change_summary(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build 模式完成后应追加总结 prompt 并从会话上下文提取修改说明上报。"""
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    build_round = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.started"),
        make_event(3, "session.next.step.ended"),
    ]
    summary_round = [
        make_event(4, "session.next.prompt.admitted"),
        make_event(5, "session.next.step.ended"),
    ]
    hold_open = Hold()
    # ADR-055 P0-5：/context 返回**消息数组**（role + content），是真取数端点
    context_messages = [
        {"role": "user", "content": [{"type": "text", "text": "实现登录功能"}]},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "# 本轮修改说明\n- 新增登录接口"}],
        },
    ]
    # ADR-055 P1-6：正常轮在流结束且处于静默态时直接判完成，**只订阅一次**
    # （旧实现会先重连一次再等静默超时），因此流序列无需为「重连」预留槽位。
    client = FakeTilacodeClient(
        streams=[build_round, summary_round, [hold_open]],
        active=False,
        context_messages=context_messages,
    )
    callbacks, calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    await bridge.execute_task(make_payload())

    # 正常轮 + 总结轮各订阅一次，均以 after=None 首订
    assert client.stream_calls == [None, None]

    # 第二次 prompt 为总结指令，发往同一 Session
    assert len(client.prompts) == 2
    assert client.prompts[1][0] == "ses_task_t1"
    assert "本轮修改说明" in client.prompts[1][1]
    # 完成回调携带从会话上下文提取的最后一条 assistant 文本
    assert calls["completed"] == [("t1", "t1", None, "# 本轮修改说明\n- 新增登录接口")]
    # 总结轮不上报进度（progress 序列仅来自正常轮：20 → step.ended +2）
    assert [(item[0], item[1], item[2]) for item in calls["progress"]] == [
        ("t1", 20, None),
        ("t1", 22, None),
    ]


async def test_change_summary_falls_back_to_history_events(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/context` 无可用文本时回退 `/history` durable 事件抽取（ADR-055 P0-5）。

    ``/history`` 返回的是事件分页而非消息数组；旧实现按消息解析必然取不到
    文本——本用例锁定「从 `session.next.text.ended` 事件抽取」的回退能力。
    """
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    build_round = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.started"),
        make_event(3, "session.next.step.ended"),
    ]
    summary_round = [
        make_event(4, "session.next.prompt.admitted"),
        make_event(5, "session.next.step.ended"),
    ]
    client = FakeTilacodeClient(
        streams=[build_round, summary_round, [Hold()]],
        active=False,
        context_messages=[{"role": "user", "content": [{"type": "text", "text": "实现登录功能"}]}],
        history=[
            make_event(6, "session.next.text.ended", {"text": "# 事件回退说明"}),
        ],
    )
    callbacks, calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    await bridge.execute_task(make_payload())

    assert calls["completed"] == [("t1", "t1", None, "# 事件回退说明")]


async def test_change_summary_none_when_no_assistant_text(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两路取数都没有 assistant 文本时 change_summary 为 None 且不阻断完成上报。"""
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    build_round = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.started"),
        make_event(3, "session.next.step.ended"),
    ]
    summary_round = [
        make_event(4, "session.next.prompt.admitted"),
        make_event(5, "session.next.step.ended"),
    ]
    client = FakeTilacodeClient(
        streams=[build_round, summary_round, [Hold()]],
        active=False,
        context_messages=[{"role": "user", "content": [{"type": "text", "text": "需求"}]}],
        history=[make_event(6, "session.next.step.ended", {})],
    )
    callbacks, calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    await bridge.execute_task(make_payload())

    assert calls["completed"] == [("t1", "t1", None, None)]
    assert calls["failed"] == []


# ---------------- ADR-029：任务执行模型选择与凭据下发 ----------------


async def test_execute_task_hub_model_injects_key_and_selects_model(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
    no_summary: None,
) -> None:
    """hub 来源任务：执行前注入凭据，session 按选择指定模型（I3）。"""
    payload = make_payload()
    payload.executor_model = {
        "source": "hub",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "api_key": "sk-hub-secret",
    }

    callbacks, calls = make_callbacks()
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    first = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.ended"),
    ]
    client = FakeTilacodeClient(streams=[first, [Hold()]], active=False)
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )
    # 派发前预检（ADR-061 C1）：内置 provider 也必须在 tilacode 模型目录里
    client.provider_snapshot_data = {"deepseek": ["deepseek-chat"]}
    await bridge.execute_task(payload)

    assert client.integration_keys == [("deepseek", "sk-hub-secret")]
    # 凭据注入不带任务目录：tilacode 凭据为全局存储，新任务目录的 location
    # 懒初始化竞态会导致带目录注入 500（Key method not found）
    assert client.integration_key_kwargs == [{}]
    assert client.created[0]["model"] == {"providerID": "deepseek", "id": "deepseek-chat"}
    # 实际执行模型随 task_started 上报（ADR-029 增强）
    assert calls["task_started"] == [("t1", "deepseek/deepseek-chat")]


async def test_execute_task_custom_model_injects_key_and_selects_model(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
    no_summary: None,
) -> None:
    """custom 来源任务：按指定模型行注入凭据并指定模型（ADR-030）。"""
    payload = make_payload()
    payload.executor_model = {
        "source": "custom",
        "provider_id": "provider-uuid",
        "provider": "siliconflow",
        "model": "deepseek-v4-flash",
        "api_key": "sk-custom-secret",
        "base_url": "https://api.siliconflow.cn/v1",
    }

    callbacks, calls = make_callbacks()
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    first = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.ended"),
    ]
    client = FakeTilacodeClient(streams=[first, [Hold()]], active=False)
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )
    # 派发前预检（ADR-061 C1）：目录里必须有本任务注入的 provider 与模型
    from app.executor.workspace import provider_namespace

    client.provider_snapshot_data = {
        provider_namespace("siliconflow", payload.task_id): ["deepseek-v4-flash"]
    }
    await bridge.execute_task(payload)

    assert client.integration_keys == []
    # ADR-055 D3 修订（2026-09-27）：带 base_url 的自定义模型 provider 定义
    # **只写入任务工作目录项目配置**——tilacode 随会话动态加载项目配置且接受
    # 单数 provider 键；用户级配置在新版 tilacode 上被严格 schema
    # 校验拒绝（Unrecognized keys → server 启动即崩），故**不再写入**；
    # 不再调 integration connect/key
    import json as _json

    user_config_path = workspace.config_dir / "tilacode.jsonc"
    assert not user_config_path.exists()
    ws_config_path = Path(workspace.workspace_root) / "t1" / ".tilacode" / "tilacode.jsonc"
    ws_config = _json.loads(ws_config_path.read_text(encoding="utf-8"))
    providers = ws_config["provider"]
    # 顶层只允许 ConfigV1.Info 已知键：任一未知键（如历史版本的
    # _hub_managed_providers 毒键）会让 ConfigParse.schema 以
    # unrecognized_keys 拒绝整个文件，provider 随之丢失（parse.ts
    # topLevelExtraKeys，2026-09-27 二次排查定案）
    assert "_hub_managed_providers" not in ws_config
    assert len(providers) == 1
    namespace_name, provider_cfg = next(iter(providers.items()))
    assert namespace_name.startswith("hub-siliconflow-")
    assert provider_cfg["npm"] == "@ai-sdk/openai-compatible"
    assert provider_cfg["options"]["baseURL"] == "https://api.siliconflow.cn/v1"
    assert provider_cfg["options"]["apiKey"] == "sk-custom-secret"
    assert "deepseek-v4-flash" in provider_cfg["models"]
    # provider 写入后必须触发目录实例失效（PATCH /config），否则运行中
    # tilacode 不感知配置变更（ModelUnavailableError 修复的必要环节）
    assert client.config_invalidations == [str(Path(workspace.workspace_root) / "t1")]
    assert client.created[0]["model"] == {
        "providerID": namespace_name,
        "id": "deepseek-v4-flash",
    }
    assert calls["task_started"] == [("t1", "siliconflow/deepseek-v4-flash")]


async def test_execute_task_missing_executor_model_falls_back(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
    no_summary: None,
) -> None:
    """旧格式 payload（无 executor_model）：行为与升级前一致（I5）。"""
    callbacks, calls = make_callbacks()
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    first = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.ended"),
    ]
    client = FakeTilacodeClient(streams=[first, [Hold()]], active=False)
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )
    await bridge.execute_task(make_payload())

    assert client.integration_keys == []
    assert client.created[0]["model"] is None
    assert calls["task_started"] == [("t1", "opencode/mimo-v2.5-free")]


async def test_execute_task_hub_model_without_key_degrades(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
    no_summary: None,
) -> None:
    """hub 来源但缺少 api_key：跳过注入并回退免费通道（降级链路）。"""
    payload = make_payload()
    payload.executor_model = {"source": "hub", "model": "deepseek-chat"}

    callbacks, calls = make_callbacks()
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    first = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.ended"),
    ]
    client = FakeTilacodeClient(streams=[first, [Hold()]], active=False)
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )
    await bridge.execute_task(payload)

    assert client.integration_keys == []
    assert client.created[0]["model"] is None
    # 降级描述注明原因，供中枢任务详情展示排查
    assert calls["task_started"] == [("t1", "opencode/mimo-v2.5-free（中枢未下发凭据，降级）")]


# ---------------- 产物拉取工作目录反查（ADR-028 / ADR-036 回归） ----------------


async def test_file_fetch_resolves_explicit_output_dir(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
) -> None:
    """显式 output_dir 任务的 file_fetch 必须解析到执行时目录。

    ADR-036 起新链任务由中枢下发显式 output_dir（如 `静态登录页面-7677bf95`）；
    反查若只回放 project_key 会 prepare 出默认空目录，所有按需拉取恒报
    「文件不存在」（清单正常、预览失败的症状）。
    """
    callbacks, calls = make_callbacks()
    bridge = TaskBridge(
        config, FakeTilacodeClient(), SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )
    task_id = "7677bf95-e9a8-4950-ba49-926a13127a30"
    bridge._task_project_keys[task_id] = (task_id, "静态登录页面-7677bf95")

    ws_dir = workspace.prepare(task_id, output_dir="静态登录页面-7677bf95")
    doc_dir = ws_dir / "specs" / "静态登录页面（单文件-index.htm-7677bf95"
    doc_dir.mkdir(parents=True)
    (doc_dir / "tasks.md").write_text("# 任务清单", encoding="utf-8")

    await bridge.handle_file_fetch(
        "req-1", task_id, "file", "specs/静态登录页面（单文件-index.htm-7677bf95/tasks.md"
    )

    assert calls["file_error"] == []
    assert len(calls["file_chunk"]) == 1
    request_id, seq, total, data, done = calls["file_chunk"][0]
    assert (request_id, seq, total, done) == ("req-1", 0, 1, True)
    assert base64.b64decode(data).decode("utf-8") == "# 任务清单"


async def test_file_fetch_without_output_dir_uses_default_dir(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
) -> None:
    """无显式 output_dir（默认目录）任务的拉取路径保持不变。"""
    callbacks, calls = make_callbacks()
    bridge = TaskBridge(
        config, FakeTilacodeClient(), SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )
    task_id = "t-default"
    bridge._task_project_keys[task_id] = (task_id, None)

    ws_dir = workspace.prepare(task_id)
    (ws_dir / "out.txt").write_text("hello", encoding="utf-8")

    await bridge.handle_file_fetch("req-2", task_id, "file", "out.txt")

    assert calls["file_error"] == []
    assert base64.b64decode(calls["file_chunk"][0][3]).decode("utf-8") == "hello"


async def test_file_fetch_survives_restart_via_persisted_mapping(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
) -> None:
    """Agent 重启后（内存映射丢失）file_fetch 仍能反查工作目录（2026-09-23）。

    复现路径：进程重启 → _task_project_keys 清空 → 旧实现回退默认空目录
    解析，恒报「文件不存在」而清单仍显示历史文件。修复后从持久化映射
    （task_id → project_key/output_dir）与 SessionStore（执行时绝对目录）
    恢复反查。
    """
    mapping_path = tmp_path / "task_mappings.json"
    task_id = "7677bf95-e9a8-4950-ba49-926a13127a30"

    # —— 第一次进程：执行任务，写入内存 + 持久化映射 ——
    first_bridge = TaskBridge(
        config,
        FakeTilacodeClient(),
        SessionStore(tmp_path / "sessions.json"),
        workspace,
        make_callbacks()[0],
        mapping_store=TaskMappingStore(mapping_path),
    )
    first_bridge._task_project_keys[task_id] = (task_id, "静态登录页面-7677bf95")
    await first_bridge._mapping_store.put(task_id, task_id, "静态登录页面-7677bf95")
    ws_dir = workspace.prepare(task_id, output_dir="静态登录页面-7677bf95")
    (ws_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    # 执行时 SessionStore 登记 workspace_dir（模拟 _prepare_session 的 upsert）
    first_store = SessionStore(tmp_path / "sessions.json")
    await first_store.upsert(
        task_id, session_id="sess-1", workspace_dir=str(ws_dir), status="completed"
    )

    # —— 第二次进程：内存映射清空（模拟重启），仅持久化留存 ——
    second_callbacks, second_calls = make_callbacks()
    second_bridge = TaskBridge(
        config,
        FakeTilacodeClient(),
        SessionStore(tmp_path / "sessions.json"),
        workspace,
        second_callbacks,
        mapping_store=TaskMappingStore(mapping_path),
    )
    assert not second_bridge._task_project_keys

    await second_bridge.handle_file_fetch("req-9", task_id, "file", "index.html")

    assert second_calls["file_error"] == []
    assert base64.b64decode(second_calls["file_chunk"][0][3]).decode("utf-8") == "<html></html>"


# ---------------- ADR-039 P2：附件落盘与 zip 安全解压 ----------------


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    """构造内存 zip 压缩包测试样本。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _attachment_info(
    attachment_id: str,
    file_name: str,
    mime: str = "application/zip",
    size: int = 0,
) -> TaskAttachmentInfo:
    """构造派发 payload 的附件信息。"""
    return TaskAttachmentInfo(
        id=attachment_id,
        file_name=file_name,
        mime=mime,
        size=size,
        url=f"/api/v1/tasks/t1/attachments/{attachment_id}?token=x",
    )


def _persist_bridge(config: AgentConfig, workspace: WorkspaceManager) -> TaskBridge:
    """构造仅用于附件落盘单测的 bridge（依赖均为替身）。"""
    return TaskBridge(
        config,
        MagicMock(),
        MagicMock(),
        workspace,
        MagicMock(),
    )


async def test_persist_attachments_extracts_zip(
    config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """zip 附件解压到 .hub-attachments/{id8}_{stem}/ 目录（P2）。"""
    bridge = _persist_bridge(config, workspace)
    ws_dir = workspace.prepare("t-zip")
    payload = make_payload("t-zip")
    payload.attachment_blobs = [
        (
            _attachment_info("aaaaaaaa-1111-2222-3333-444444444444", "demo.zip"),
            _zip_bytes({"src/main.py": "print('hi')".encode(), "README.md": b"readme"}),
        )
    ]

    saved = bridge._persist_attachments(ws_dir, payload)

    assert len(saved) == 1
    target_dir = saved[0]
    assert target_dir.name == "aaaaaaaa_demo"
    assert (target_dir / "src" / "main.py").read_text() == "print('hi')"
    assert (target_dir / "README.md").read_text() == "readme"


async def test_persist_attachments_rejects_zip_slip(
    config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """zip 含 ``..`` 越界成员：整体拒绝并清理半成品目录（UT-10）。"""
    bridge = _persist_bridge(config, workspace)
    ws_dir = workspace.prepare("t-slip")
    payload = make_payload("t-slip")
    payload.attachment_blobs = [
        (
            _attachment_info("bbbbbbbb-1111-2222-3333-444444444444", "evil.zip"),
            _zip_bytes({"../evil.txt": b"pwn", "ok.txt": b"fine"}),
        )
    ]

    saved = bridge._persist_attachments(ws_dir, payload)

    assert saved == []
    # 半成品目录已被清理，越界文件未落到工作目录任何位置
    assert not (ws_dir / ".hub-attachments").exists() or not any(
        (ws_dir / ".hub-attachments").rglob("evil.txt")
    )


async def test_persist_attachments_rejects_absolute_member(
    config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """zip 含绝对路径成员：拒绝解压（zip slip 防护）。"""
    bridge = _persist_bridge(config, workspace)
    ws_dir = workspace.prepare("t-abs")
    payload = make_payload("t-abs")
    payload.attachment_blobs = [
        (
            _attachment_info("cccccccc-1111-2222-3333-444444444444", "abs.zip"),
            _zip_bytes({"/etc/passwd": b"pwn"}),
        )
    ]

    saved = bridge._persist_attachments(ws_dir, payload)

    assert saved == []


async def test_persist_attachments_plain_image(
    config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """图片附件直接写入 .hub-attachments/（P1 逻辑回归）。"""
    bridge = _persist_bridge(config, workspace)
    ws_dir = workspace.prepare("t-img")
    payload = make_payload("t-img")
    payload.attachment_blobs = [
        (
            _attachment_info(
                "dddddddd-1111-2222-3333-444444444444",
                "bug.png",
                mime="image/png",
            ),
            b"\x89PNG-bytes",
        )
    ]

    saved = bridge._persist_attachments(ws_dir, payload)

    assert len(saved) == 1
    assert saved[0].read_bytes() == b"\x89PNG-bytes"


# ---------------------------------------------------------------------------
# 完成闸与自修（ADR-061 M4）
# ---------------------------------------------------------------------------


def _verify_report(
    status: str,
    *,
    source: str = "verify_json",
    failures: list[tuple[str, str, str]] | None = None,
) -> verify_runner.VerifyReport:
    """构造验收报告：failures 为 (layer, name, cmd) 三元组（皆为必过失败）。"""
    checks = [
        verify_runner.CheckResult(
            layer=layer,
            name=name,
            cmd=cmd,
            cwd=".",
            must_pass=True,
            status="failed",
            exit_code=1,
            duration_ms=5,
            output_tail=f"{cmd} 的输出尾部",
        )
        for layer, name, cmd in (failures or [])
    ]
    return verify_runner.VerifyReport(status=status, source=source, checks=checks)


def _gate_bridge(
    config: AgentConfig,
    workspace: WorkspaceManager,
    tmp_path: Path,
    client: FakeTilacodeClient,
) -> tuple[TaskBridge, dict]:
    """构造用于闸门测试的 bridge（fake 客户端；不启动 SSE）。"""
    callbacks, calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )
    return bridge, calls


async def test_completion_gate_passes_without_repair(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-M4-5a：必过层全绿 → 直接放行，**不**触发任何自修轮。"""
    client = FakeTilacodeClient()
    bridge, calls = _gate_bridge(config, workspace, tmp_path, client)
    bridge._run_session = AsyncMock()  # type: ignore[method-assign]
    verify_runner.load_plan = lambda *_: ({"checks": []}, "verify_json")
    verify_runner.run_plan = AsyncMock(return_value=_verify_report("passed"))

    report = await bridge._run_completion_gate(
        make_payload("t1"), "ses_task_t1", tmp_path, "t1"
    )

    assert report is not None and report.status == "passed"
    bridge._run_session.assert_not_awaited()
    assert calls["verify_blocked"] == []


async def test_completion_gate_repairs_then_passes(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-M4-6：必过层红 → 回灌失败信息自修 → 复验通过即放行（≤5 轮内）。"""
    client = FakeTilacodeClient()
    bridge, calls = _gate_bridge(config, workspace, tmp_path, client)
    bridge._run_session = AsyncMock()  # type: ignore[method-assign]
    verify_runner.load_plan = lambda *_: ({"checks": []}, "verify_json")
    verify_runner.run_plan = AsyncMock(
        side_effect=[
            _verify_report("failed", failures=[("L1", "单测", "pytest -q")]),
            _verify_report("passed"),
        ]
    )

    report = await bridge._run_completion_gate(
        make_payload("t1"), "ses_task_t1", tmp_path, "t1"
    )

    assert report is not None and report.status == "passed"
    bridge._run_session.assert_awaited_once()
    # 自修提示必须带失败命令与输出尾部（供模型定位）
    prompt = bridge._run_session.await_args.args[2]
    assert "pytest -q" in prompt and "输出尾部" in prompt
    assert calls["verify_blocked"] == []


async def test_completion_gate_blocks_after_repair_exhausted(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-M4-6b：持续红 → 自修用尽（5 轮）→ 上报阻断并**不**放行。"""
    client = FakeTilacodeClient()
    bridge, calls = _gate_bridge(config, workspace, tmp_path, client)
    bridge._run_session = AsyncMock()  # type: ignore[method-assign]
    verify_runner.load_plan = lambda *_: ({"checks": []}, "verify_json")
    verify_runner.run_plan = AsyncMock(
        return_value=_verify_report("failed", failures=[("L0", "类型检查", "npx tsc --noEmit")])
    )

    report = await bridge._run_completion_gate(
        make_payload("t1"), "ses_task_t1", tmp_path, "t1"
    )

    assert report is None
    assert bridge._run_session.await_count == 5
    assert len(calls["verify_blocked"]) == 1
    _task_id, _project_key, payload = calls["verify_blocked"][0]
    assert payload["repair_rounds"] == 5
    assert payload["must_pass_failures"][0]["cmd"] == "npx tsc --noEmit"


async def test_completion_gate_blocks_unverifiable_without_repair(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-M4-4c：**无判据 → 直接阻断、不自修**（代码修不出「判据」）。"""
    client = FakeTilacodeClient()
    bridge, calls = _gate_bridge(config, workspace, tmp_path, client)
    bridge._run_session = AsyncMock()  # type: ignore[method-assign]
    verify_runner.load_plan = lambda *_: (None, "none")
    verify_runner.run_plan = AsyncMock(return_value=_verify_report("unverifiable", source="none"))

    report = await bridge._run_completion_gate(
        make_payload("t1"), "ses_task_t1", tmp_path, "t1"
    )

    assert report is None
    bridge._run_session.assert_not_awaited()
    assert len(calls["verify_blocked"]) == 1
    assert calls["verify_blocked"][0][2]["status"] == "unverifiable"


async def test_completion_gate_detects_probe_created_during_work_phase(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-M4-6c（2026-10-03 修订）：模型在 gate 之前（工作阶段）自建探针也应被检出。

    回归对象：初版基线在 gate 启动时才拍——模型按需求提示在首轮工作阶段写好
    check_probe.py，gate 快照时探针已存在，复查无变化，自证通过被呈现为正常
    完成（ADR-061 验收②实测）。修订后基线在 _prepare_session（prompt 下发前）
    拍摄暂存于 ``_tamper_baselines``，gate 弹出使用，工作阶段新建同样落网。
    """
    client = FakeTilacodeClient()
    bridge, calls = _gate_bridge(config, workspace, tmp_path, client)
    bridge._run_session = AsyncMock()  # type: ignore[method-assign]
    verify_runner.load_plan = lambda *_: ({"checks": []}, "verify_json")
    verify_runner.run_plan = AsyncMock(return_value=_verify_report("passed"))

    # 模拟 _prepare_session：prompt 下发前拍基线（此刻工作区干净，无探针）
    bridge._tamper_baselines["t1"] = verify_tamper.snapshot(tmp_path, "specs/t1-demo")
    # 模型在工作阶段自建探针（gate 快照拍摄之前的形态）
    tools = tmp_path / ".tilacode" / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "check_probe.py").write_text("print('ok')")

    report = await bridge._run_completion_gate(
        make_payload("t1"), "ses_task_t1", tmp_path, "t1"
    )

    assert report is None
    bridge._run_session.assert_not_awaited()
    assert len(calls["verify_blocked"]) == 1
    blocked = calls["verify_blocked"][0][2]
    assert blocked["status"] == "unverifiable"
    assert "check_probe.py" in blocked["summary"]
    # 基线已弹出，不会跨任务泄漏
    assert bridge._tamper_baselines == {}


async def test_dispatch_preflight_fails_fast_when_model_missing(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
    no_summary: None,
) -> None:
    """派发前预检（ADR-061 C1）：目录里没有该 provider/模型 → 立即失败、不建会话。

    2026-10-01 事故的护栏：以前这种情况要等 ``task_startup_stalled``（60s）
    才丢一句 ModelUnavailableError；现在在 create_session 之前就明确失败，
    并给出已发现的 provider 清单供排查。
    """
    payload = make_payload()
    payload.executor_model = {
        "source": "custom",
        "provider": "siliconflow",
        "model": "deepseek-v4-flash",
        "api_key": "sk-custom-secret",
        "base_url": "https://api.siliconflow.cn/v1",
    }
    callbacks, calls = make_callbacks()
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    client = FakeTilacodeClient(streams=[[Hold()]])
    # 目录非空（tilacode 内置 provider 在）但缺本任务注入的 provider
    client.provider_snapshot_data = {"opencode": ["deepseek-v4-flash-free"]}
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    await bridge.execute_task(payload)

    # 预检在 create_session 之后执行（2026-10-02 顺序勘误：invalidate 后
    # 实例尚未重建，重建前查询目录恒为空）——会话已创建但未下发 prompt、
    # 任务快速失败，不产生 LLM 消耗
    assert len(client.created) == 1
    assert client.preflights == [str(Path(workspace.workspace_root) / "t1")]
    assert len(calls["failed"]) == 1
    _task_id, reason = calls["failed"][0]
    assert "无法解析执行模型" in reason
    assert "hub-siliconflow-" in reason


async def test_dispatch_preflight_flags_empty_catalog(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
    no_summary: None,
) -> None:
    """派发前预检（ADR-061 C1）：目录整体为空（旧产物/实例未就绪）→ 明确失败。"""
    payload = make_payload()
    payload.executor_model = {
        "source": "custom",
        "provider": "siliconflow",
        "model": "deepseek-v4-flash",
        "api_key": "sk-custom-secret",
        "base_url": "https://api.siliconflow.cn/v1",
    }
    callbacks, calls = make_callbacks()
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    client = FakeTilacodeClient(streams=[[Hold()]])
    client.provider_snapshot_data = {}
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    await bridge.execute_task(payload)

    # 预检在 create_session 之后执行（2026-10-02 顺序勘误）——会话已创建
    # 但未下发 prompt、任务快速失败
    assert len(client.created) == 1
    assert len(calls["failed"]) == 1
    assert "模型目录为空" in calls["failed"][0][1]


async def test_execute_task_reports_status_trace_rows(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
    no_summary: None,
) -> None:
    """ADR-063 M1：派发开始与会话结束各上报 1 条 `status` 轨迹行，seq 从 1 递增。

    回归对象：M1 只打通成功链路的 status 行——「启动」需带实际执行模型与工作目录，
    「结束」为纯收尾行，且任务结束后 seq 记录被清理（不随任务数无限增长）。
    """
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    build_round = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.started"),
        make_event(3, "session.next.step.ended"),
    ]
    client = FakeTilacodeClient(streams=[build_round, [Hold()]], active=False)
    callbacks, calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    await bridge.execute_task(make_payload())

    assert calls["completed"] == [("t1", "t1", None, None)]
    assert calls["failed"] == []
    # 两条 status 行：任务 ID 与会话 ID 归属一致（会话 ID 为节点侧 ses_task_{项目键}）
    assert [(item[0], item[2]) for item in calls["agent_trace"]] == [
        ("t1", "ses_task_t1"),
        ("t1", "ses_task_t1"),
    ]
    _task_id, node_id, _session_id, first_rows = calls["agent_trace"][0]
    # node_id 由 HubClient 侧回退为本节点已注册 ID，桥接层不感知 → 传 None
    assert node_id is None
    assert first_rows == [
        {
            "seq": 1,
            "kind": "status",
            "payload": {
                "stage": "启动",
                "message": "任务已派发到节点",
                "model": _FREE_MODEL_DESC,
                "directory": str(Path(workspace.workspace_root) / "t1"),
            },
        }
    ]
    assert calls["agent_trace"][1][3] == [
        {
            "seq": 2,
            "kind": "status",
            "payload": {"stage": "结束", "message": "会话已结束"},
        }
    ]
    # 任务收尾清理轨迹采集器（避免字典随任务数增长）
    assert bridge._trace_collectors == {}


async def test_execute_task_survives_agent_trace_failure(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
    no_summary: None,
) -> None:
    """ADR-063 M1 旁路保证：轨迹上报抛异常时任务照常完成（完成判定/回调不受影响）。"""
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    build_round = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.started"),
        make_event(3, "session.next.step.ended"),
    ]
    client = FakeTilacodeClient(streams=[build_round, [Hold()]], active=False)
    callbacks, calls = make_callbacks()

    async def _exploding_report(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("轨迹上报炸了")

    callbacks.on_agent_trace = _exploding_report
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    await bridge.execute_task(make_payload())

    assert calls["completed"] == [("t1", "t1", None, None)]
    assert calls["failed"] == []
    assert bridge._trace_collectors == {}


async def test_execute_task_streams_reasoning_and_tool_trace_rows(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
    no_summary: None,
) -> None:
    """ADR-063 M2：会话事件被分类为 reasoning/tool/text 轨迹行，与 status 行共用单调 seq。

    回归对象：思考增量分段 + ended 收尾去重、tool.success 经 callID 关联
    工具名并带结果摘要与退出码、正文 ended 全文、轨迹行整体 seq 连续。
    """
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    build_round = [
        make_event(1, "session.next.prompt.admitted"),
        make_event(2, "session.next.step.started"),
        make_event(
            3,
            "session.next.reasoning.delta",
            {"reasoningID": "r1", "delta": "思" * 1300},
        ),
        make_event(
            4,
            "session.next.reasoning.ended",
            {"reasoningID": "r1", "text": "思" * 1300 + "，结论：先看配置"},
        ),
        make_event(
            5,
            "session.next.tool.called",
            {"tool": "bash", "callID": "c1", "input": {"command": "npm test"}},
        ),
        make_event(
            6,
            "session.next.tool.success",
            {
                "callID": "c1",
                "content": [{"type": "text", "text": "3 passed"}],
                "structured": {"exit": 0},
            },
        ),
        make_event(7, "session.next.text.ended", {"textID": "t1", "text": "开发完成"}),
        make_event(8, "session.next.step.ended"),
    ]
    client = FakeTilacodeClient(streams=[build_round, [Hold()]], active=False)
    callbacks, calls = make_callbacks()
    bridge = TaskBridge(
        config, client, SessionStore(tmp_path / "sessions.json"), workspace, callbacks
    )

    await bridge.execute_task(make_payload())

    assert calls["completed"] == [("t1", "t1", None, None)]
    rows = [row for _t, _n, _s, batch in calls["agent_trace"] for row in (batch or [])]
    # 与 M1 status 行共用 seq：任务内从 1 连续单调
    assert [row["seq"] for row in rows] == list(range(1, len(rows) + 1))
    kinds = [row["kind"] for row in rows]
    assert kinds.count("status") == 2
    assert kinds.count("reasoning") == 2
    assert kinds.count("tool") == 2
    assert kinds.count("text") == 1
    assert kinds[0] == "status" and kinds[-1] == "status"
    reasoning_rows = [row for row in rows if row["kind"] == "reasoning"]
    assert reasoning_rows[0]["payload"]["text"] == "思" * 1300
    assert reasoning_rows[1]["payload"]["text"] == "，结论：先看配置"
    tool_rows = [row for row in rows if row["kind"] == "tool"]
    assert tool_rows[0]["payload"] == {
        "tool": "bash",
        "phase": "called",
        "call_id": "c1",
        "target": "npm test",
    }
    assert tool_rows[1]["payload"]["tool"] == "bash"  # tool.success 无名字，经 callID 回查
    assert tool_rows[1]["payload"]["phase"] == "success"
    assert tool_rows[1]["payload"]["summary"] == "3 passed"
    assert tool_rows[1]["payload"]["exit_code"] == 0
    text_row = next(row for row in rows if row["kind"] == "text")
    assert text_row["payload"]["text"] == "开发完成"
