"""ADR-064 M2：节点续聊对话单元测试。

覆盖任务书 UT-N1~N6：

- UT-N1 ``send_prompt`` delivery 参数化（默认 steer 保持现行为；续聊传 queue）；
- UT-N2 会话缺失（登记簿无记录 / tilacode 探测失败）→ error("session_missing")；
- UT-N3 临时轨迹采集器 seq 自 last_seq+1 接续、上报归属 trace_task_id、
  轮次结束排水销毁（不进 ``_trace_collectors``）；
- UT-N4 回复提取与上行（assistant 全文 + tokens）、独立超时路径（error("timeout")
  + 尽力中断）；
- UT-N5 红线：不触碰任务状态（_current_tasks/_consumers/_trace_collectors/
  登记簿状态全程不变、无任务进度上报）；
- UT-N6 busy 并发保护（同一会话进行中续聊轮时新请求直接回 error("busy")）。

另覆盖 HubClient 侧：``hub.node_chat`` 下行分发（异步不阻塞）、payload 校验、
桥接未就绪回错、上行 reply/error 事件 payload 契约与回调绑定。

全部基于 fake client / fake WebSocket，无真实网络。
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.client.hub_client import HubClient
from app.config import AgentConfig
from app.executor.session_store import SessionStore
from app.executor.task_bridge import BridgeCallbacks, TaskBridge
from app.executor.tilacode_client import TilacodeAPIError, TilacodeClient
from app.executor.workspace import WorkspaceManager
from app.models.schemas import TaskDispatchPayload

BASE_URL = "http://127.0.0.1:4096"


def make_event(seq: int, event_type: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """构造带 durable.seq 的 tilacode SSE 事件。"""
    return {
        "id": f"evt_{seq}",
        "type": event_type,
        "data": data or {},
        "durable": {"aggregateID": "ses_task_root", "seq": seq, "version": 1},
    }


class Hold:
    """事件流中的挂起点：release 前阻塞消费循环，用于控制时序。"""

    def __init__(self) -> None:
        self.event = asyncio.Event()

    def release(self) -> None:
        self.event.set()


class FakeTilacodeClient:
    """测试用 tilacode 客户端：记录续聊相关调用并按会话回放预置事件流。"""

    def __init__(
        self,
        streams: list[list[Any]] | None = None,
        active: bool = False,
        context_messages: list[dict[str, Any]] | None = None,
        context_error: TilacodeAPIError | None = None,
        instance_events: list[Any] | None = None,
    ) -> None:
        self.prompts: list[tuple[str, str, str]] = []  # (session_id, text, delivery)
        self.interrupts: list[str] = []
        self.stream_calls: list[int | None] = []
        self._session_stream_index: dict[str, int] = {}
        self._streams = streams or []
        self._active = active
        self._context_messages = context_messages or []
        self._context_error = context_error
        self._instance_events = instance_events or []

    async def close(self) -> None:
        """空实现：fake 无底层连接。"""

    async def get_context(
        self, session_id: str, *, directory: str | None = None, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """会话上下文消息数组（存在性探测 + 回复提取共用端点）。"""
        if self._context_error is not None:
            raise self._context_error
        return self._context_messages

    async def send_prompt(self, session_id: str, text: str, **kwargs: Any) -> dict[str, Any]:
        self.prompts.append((session_id, text, str(kwargs.get("delivery", "steer"))))
        return {"data": {"sessionID": session_id}}

    async def interrupt(self, session_id: str, **kwargs: Any) -> None:
        self.interrupts.append(session_id)

    async def is_session_active(self, session_id: str, **kwargs: Any) -> bool:
        return self._active

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
        for item in self._instance_events:
            yield item


def make_recorders() -> tuple[BridgeCallbacks, dict[str, list[Any]]]:
    """构造记录型回调集合（含续聊回复/错误与轨迹上报，ADR-064 M2）。"""
    calls: dict[str, list[Any]] = {
        "progress": [],
        "completed": [],
        "failed": [],
        "agent_trace": [],
        "node_chat_reply": [],
        "node_chat_error": [],
    }

    async def _record_progress(*args: Any, **kwargs: Any) -> None:
        calls["progress"].append((args, kwargs))

    async def _record_completed(*args: Any, **kwargs: Any) -> None:
        calls["completed"].append((args, kwargs))

    async def _record_failed(*args: Any, **kwargs: Any) -> None:
        calls["failed"].append((args, kwargs))

    async def _record_agent_trace(*args: Any, **kwargs: Any) -> None:
        calls["agent_trace"].append((args, kwargs))

    async def _record_reply(**kwargs: Any) -> None:
        calls["node_chat_reply"].append(dict(kwargs))

    async def _record_error(**kwargs: Any) -> None:
        calls["node_chat_error"].append(dict(kwargs))

    callbacks = BridgeCallbacks(
        on_progress=_record_progress,
        on_task_started=_record_completed,  # 续聊路径不会调用，占位即可
        on_completed=_record_completed,
        on_failed=_record_failed,
        on_file_tree=_record_progress,
        on_file_chunk=_record_progress,
        on_file_error=_record_failed,
        on_agent_trace=_record_agent_trace,
        on_node_chat_reply=_record_reply,
        on_node_chat_error=_record_error,
    )
    return callbacks, calls


def make_chat_payload(root: str = "root", **overrides: Any) -> dict[str, Any]:
    """构造标准 ``hub.node_chat`` 负载（契约见 ADR-064 事件表）。"""
    payload: dict[str, Any] = {
        "task_id": "t-last",
        "root_task_id": root,
        "node_id": "node-1",
        "text": "总结一下刚才的改动",
        "reply_to": "reply-1",
        "trace_task_id": "t-last",
        "last_seq": 5,
    }
    payload.update(overrides)
    return payload


async def _wait_until(predicate: Any, timeout: float = 2.0) -> None:
    """轮询等待条件成立（并发时序测试用），超时断言失败。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("等待条件超时")
        await asyncio.sleep(0.01)


async def _drain_chat_tasks(hub: HubClient) -> None:
    """等待 HubClient 上所有在途续聊任务结束。"""
    tasks = list(hub._node_chat_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# UT-N1：send_prompt delivery 参数化
# ---------------------------------------------------------------------------


@respx.mock
async def test_send_prompt_defaults_to_steer_delivery() -> None:
    """UT-N1a：默认 delivery=steer（保持既有任务行为），显式可传 queue。"""
    client = TilacodeClient(BASE_URL, lambda: "secret-password", "/workspace/task-1")
    route = respx.post(f"{BASE_URL}/api/session/ses_1/prompt").mock(
        return_value=httpx.Response(200, json={"data": {"sessionID": "ses_1"}})
    )
    try:
        await client.send_prompt("ses_1", "定向发言")
        assert json.loads(route.calls[0].request.content)["delivery"] == "steer"

        await client.send_prompt("ses_1", "续聊发言", delivery="queue")
        body = json.loads(route.calls[1].request.content)
        assert body["delivery"] == "queue"
        assert body["prompt"] == {"text": "续聊发言"}
    finally:
        await client.close()


@pytest.fixture
def config() -> AgentConfig:
    """测试配置：静默判定时长压缩到 0.1 秒加速完成判定。"""
    return AgentConfig(
        agent_registration_token="test-token",
        tilacode_step_idle_timeout_seconds=0.1,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceManager:
    """指向临时目录的工作目录管理器（续聊路径不做 prepare，仅占位装配）。"""
    return WorkspaceManager(str(tmp_path / "ws"), config_dir=tmp_path / "cfg")


async def _register_session(store: SessionStore, root: str, ws_dir: Path) -> None:
    """在登记簿登记可续聊的会话记录（与 execute_task 落库同构）。"""
    await store.upsert(
        root,
        session_id=f"ses_task_{root}",
        workspace_dir=str(ws_dir),
        status="completed",
    )


def chat_round_stream() -> list[dict[str, Any]]:
    """构造一轮续聊的会话事件流：旧轮历史（排水）→ admitted → 活动 → 结束。"""
    return [
        make_event(1, "session.next.tool.called", {"tool": "edit", "input": {"filePath": "a.py"}}),
        make_event(2, "session.next.prompt.admitted"),
        make_event(3, "session.next.tool.called", {"tool": "bash", "callID": "c1"}),
        make_event(4, "session.next.text.ended", {"textID": "tx1", "text": "回复全文"}),
        make_event(5, "session.next.step.ended", {"tokens": {"input": 10, "output": 2}}),
    ]


_REPLY_TOKENS = {
    "input": 10,
    "output": 2,
    "reasoning": None,
    "cache_read": None,
    "cache_write": None,
}


async def test_node_chat_sends_prompt_with_queue_delivery(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-N1b：续聊 prompt 以 delivery=queue 下发（空闲开新轮、活跃则入队）。"""
    client = FakeTilacodeClient(streams=[chat_round_stream()])
    callbacks, _calls = make_recorders()
    store = SessionStore(tmp_path / "sessions.json")
    await _register_session(store, "root", tmp_path / "ws-root")
    bridge = TaskBridge(config, client, store, workspace, callbacks)
    client._context_messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "回复全文"}]}
    ]

    await bridge.node_chat(make_chat_payload())

    assert client.prompts == [("ses_task_root", "总结一下刚才的改动", "queue")]


# ---------------------------------------------------------------------------
# UT-N2：会话缺失 → error("session_missing")
# ---------------------------------------------------------------------------


async def test_node_chat_missing_session_record(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-N2a：登记簿无该根任务记录 → error(session_missing)，不发 prompt。"""
    client = FakeTilacodeClient()
    callbacks, calls = make_recorders()
    store = SessionStore(tmp_path / "sessions.json")
    bridge = TaskBridge(config, client, store, workspace, callbacks)

    await bridge.node_chat(make_chat_payload())

    assert client.prompts == []
    assert client.stream_calls == []
    assert calls["node_chat_reply"] == []
    assert calls["node_chat_error"] == [
        {"reply_to": "reply-1", "task_id": "t-last", "reason": "session_missing"}
    ]


async def test_node_chat_session_probe_failed(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-N2b：tilacode 会话探测失败（如 404）→ error(session_missing)。"""
    client = FakeTilacodeClient(
        context_error=TilacodeAPIError(
            url=f"{BASE_URL}/api/session/ses_task_root/context",
            status=404,
            error_name="NotFoundError",
            message="session not found",
        )
    )
    callbacks, calls = make_recorders()
    store = SessionStore(tmp_path / "sessions.json")
    await _register_session(store, "root", tmp_path / "ws-root")
    bridge = TaskBridge(config, client, store, workspace, callbacks)

    await bridge.node_chat(make_chat_payload())

    assert client.prompts == []
    assert calls["node_chat_error"] == [
        {"reply_to": "reply-1", "task_id": "t-last", "reason": "session_missing"}
    ]


# ---------------------------------------------------------------------------
# UT-N3：临时采集器 seq 接续与销毁
# ---------------------------------------------------------------------------


async def test_node_chat_trace_collector_continues_seq_and_destroys(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-N3：轨迹行 seq 自 last_seq+1 接续、归属 trace_task_id，轮次结束销毁。"""
    client = FakeTilacodeClient(streams=[chat_round_stream()])
    callbacks, calls = make_recorders()
    store = SessionStore(tmp_path / "sessions.json")
    await _register_session(store, "root", tmp_path / "ws-root")
    bridge = TaskBridge(config, client, store, workspace, callbacks)
    client._context_messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "回复全文"}]}
    ]

    await bridge.node_chat(make_chat_payload(last_seq=5))

    # 上报归属 trace_task_id（链上最后任务），会话归属随行携带
    assert calls["agent_trace"], "续聊轮轨迹行必须上报"
    reported_seqs: list[int] = []
    for trace_args, trace_kwargs in calls["agent_trace"]:
        assert trace_args[0] == "t-last"
        rows = trace_kwargs.get("rows") or []
        reported_seqs.extend(row["seq"] for row in rows)
        for row in rows:
            assert row["kind"] in ("tool", "text", "reasoning")
    # 采集器自 last_seq+1（=6）起连续分配，不重不漏
    assert reported_seqs == sorted(reported_seqs)
    assert reported_seqs[0] == 6
    assert reported_seqs == list(range(6, 6 + len(reported_seqs)))
    assert len(reported_seqs) >= 2  # tool.called + text.ended 两类行
    # 临时采集器不进任务态字典，轮次结束即销毁（close 后全部行已排水上报）
    assert bridge._trace_collectors == {}


# ---------------------------------------------------------------------------
# UT-N4：回复提取与上行 / 独立超时路径
# ---------------------------------------------------------------------------


async def test_node_chat_replies_with_assistant_text_and_tokens(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-N4a：轮次结束后提取 assistant 全文与 step 用量上行回复。"""
    client = FakeTilacodeClient(streams=[chat_round_stream()])
    callbacks, calls = make_recorders()
    store = SessionStore(tmp_path / "sessions.json")
    await _register_session(store, "root", tmp_path / "ws-root")
    bridge = TaskBridge(config, client, store, workspace, callbacks)
    client._context_messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "回复全文"}]}
    ]

    await bridge.node_chat(make_chat_payload())

    assert calls["node_chat_error"] == []
    assert calls["node_chat_reply"] == [
        {
            "reply_to": "reply-1",
            "task_id": "t-last",
            "root_task_id": "root",
            "text": "回复全文",
            "tokens": _REPLY_TOKENS,
        }
    ]


async def test_node_chat_reply_without_tokens_omits_key(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-N4b：本轮无 step 用量明细时 tokens 键缺省（HubClient 侧契约）。"""
    client = FakeTilacodeClient(
        streams=[
            [
                make_event(1, "session.next.prompt.admitted"),
                make_event(2, "session.next.text.ended", {"textID": "tx1", "text": "plain"}),
                # 结束判定依赖 step.ended（真实轮次必有）；无 tokens 字段 → 无用量明细
                make_event(3, "session.next.step.ended"),
            ]
        ]
    )
    callbacks, calls = make_recorders()
    store = SessionStore(tmp_path / "sessions.json")
    await _register_session(store, "root", tmp_path / "ws-root")
    bridge = TaskBridge(config, client, store, workspace, callbacks)
    client._context_messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "plain"}]}
    ]

    await bridge.node_chat(make_chat_payload())

    assert calls["node_chat_reply"] == [
        {
            "reply_to": "reply-1",
            "task_id": "t-last",
            "root_task_id": "root",
            "text": "plain",
            "tokens": None,
        }
    ]


async def test_node_chat_timeout_reports_error_and_interrupts(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-N4c：整轮独立超时 → error(timeout) 并尽力中断会话（无看门狗的兜底）。"""
    monkeypatch.setattr("app.executor.task_bridge._NODE_CHAT_TIMEOUT_SECONDS", 0.05)
    hold = Hold()
    client = FakeTilacodeClient(streams=[[hold]])
    callbacks, calls = make_recorders()
    store = SessionStore(tmp_path / "sessions.json")
    await _register_session(store, "root", tmp_path / "ws-root")
    bridge = TaskBridge(config, client, store, workspace, callbacks)

    await asyncio.wait_for(bridge.node_chat(make_chat_payload()), timeout=5)

    assert calls["node_chat_reply"] == []
    assert calls["node_chat_error"] == [
        {"reply_to": "reply-1", "task_id": "t-last", "reason": "timeout"}
    ]
    assert client.interrupts == ["ses_task_root"]
    # 超时后并发保护随 finally 释放
    assert bridge._node_chat_sessions == set()


# ---------------------------------------------------------------------------
# UT-N5：红线——不触碰任务状态
# ---------------------------------------------------------------------------


async def test_node_chat_does_not_touch_task_state(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-N5：续聊全程不进任务态字典、不改登记簿状态、不触发任务进度回调。"""
    client = FakeTilacodeClient(streams=[chat_round_stream()])
    callbacks, calls = make_recorders()
    store = SessionStore(tmp_path / "sessions.json")
    await _register_session(store, "root", tmp_path / "ws-root")
    bridge = TaskBridge(config, client, store, workspace, callbacks)
    client._context_messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "回复全文"}]}
    ]
    state_before = {
        "current_tasks": dict(bridge._current_tasks),
        "consumers": dict(bridge._consumers),
        "consume_tasks": dict(bridge._consume_tasks),
        "trace_collectors": dict(bridge._trace_collectors),
        "session_dirs": dict(bridge._session_directories),
    }

    await bridge.node_chat(make_chat_payload())

    assert bridge._current_tasks == state_before["current_tasks"] == {}
    assert bridge._consumers == state_before["consumers"] == {}
    assert bridge._consume_tasks == state_before["consume_tasks"] == {}
    assert bridge._trace_collectors == state_before["trace_collectors"] == {}
    # 会话目录登记是临时的：轮次结束恢复原状
    assert bridge._session_directories == state_before["session_dirs"] == {}
    # 登记簿状态保持 completed（续聊不改任务状态机）
    record = await store.get("root")
    assert record is not None and record.status == "completed"
    # 不产生任何任务进度/完成/失败回调（那些是任务状态机通道）
    assert calls["progress"] == []
    assert calls["completed"] == []
    assert calls["failed"] == []


# ---------------------------------------------------------------------------
# UT-N6：busy 并发保护
# ---------------------------------------------------------------------------


async def test_node_chat_busy_guard_rejects_concurrent_round(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-N6：同一会话已有进行中续聊轮时，新请求直接回 error(busy) 不发话。"""
    hold = Hold()
    client = FakeTilacodeClient(streams=[[hold], [hold]])
    callbacks, calls = make_recorders()
    store = SessionStore(tmp_path / "sessions.json")
    await _register_session(store, "root", tmp_path / "ws-root")
    bridge = TaskBridge(config, client, store, workspace, callbacks)

    first = asyncio.create_task(bridge.node_chat(make_chat_payload(reply_to="reply-1")))
    await _wait_until(lambda: "ses_task_root" in bridge._node_chat_sessions)

    await bridge.node_chat(make_chat_payload(reply_to="reply-2"))

    assert calls["node_chat_error"][-1]["reason"] == "busy"
    assert calls["node_chat_error"][-1]["reply_to"] == "reply-2"
    # 第二轮未向会话发话（第一轮 prompt 是否已发与时序有关，这里只断言不超一条）
    assert len(client.prompts) <= 1

    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    assert bridge._node_chat_sessions == set()


async def test_node_chat_session_slot_released_after_round(
    tmp_path: Path, config: AgentConfig, workspace: WorkspaceManager
) -> None:
    """UT-N6b：一轮正常结束后释放并发保护位，下一轮可继续续聊。"""
    client = FakeTilacodeClient(streams=[chat_round_stream(), chat_round_stream()])
    callbacks, _calls = make_recorders()
    store = SessionStore(tmp_path / "sessions.json")
    await _register_session(store, "root", tmp_path / "ws-root")
    bridge = TaskBridge(config, client, store, workspace, callbacks)
    client._context_messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "回复全文"}]}
    ]

    await bridge.node_chat(make_chat_payload(reply_to="reply-1"))
    await bridge.node_chat(make_chat_payload(reply_to="reply-2"))

    assert [item[2] for item in client.prompts] == ["queue", "queue"]
    assert bridge._node_chat_sessions == set()


# ---------------------------------------------------------------------------
# HubClient 侧：下行分发与上行契约
# ---------------------------------------------------------------------------


class _OpenState:
    """模拟 websockets 连接的 state 对象。"""

    name = "OPEN"


class FakeWebSocket:
    """测试用 WebSocket 假连接：捕获 send 的消息。"""

    def __init__(self) -> None:
        self.state = _OpenState()
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def close(self) -> None:
        self.state.name = "CLOSED"


def sent_events(hub: HubClient) -> list[tuple[str, dict[str, Any]]]:
    """提取已发送消息的 (event, payload) 列表。"""
    ws = hub.websocket
    assert isinstance(ws, FakeWebSocket)
    return [(m["event"], m["payload"]) for m in ws.sent]


async def test_hub_dispatches_node_chat_to_bridge(config: AgentConfig) -> None:
    """hub.node_chat 应异步委托 bridge.node_chat（不阻塞接收循环、不进执行任务表）。"""
    hub = HubClient(config)
    hub.websocket = FakeWebSocket()  # type: ignore[assignment]
    hub.node_id = "node-1"
    received: list[dict[str, Any]] = []
    done = asyncio.Event()

    class FakeBridge:
        async def node_chat(self, payload: dict[str, Any]) -> None:
            received.append(payload)
            done.set()

    hub.task_bridge = FakeBridge()  # type: ignore[assignment]

    await hub._handle_message(
        json.dumps({"event": "hub.node_chat", "payload": make_chat_payload()})
    )
    await asyncio.wait_for(done.wait(), timeout=2)

    assert received == [make_chat_payload()]
    assert hub._execution_tasks == {}
    await _drain_chat_tasks(hub)


async def test_hub_node_chat_invalid_payload_is_ignored(config: AgentConfig) -> None:
    """缺 task_id/root_task_id/text/reply_to 的负载一律安全忽略（不发话、不回错）。"""
    hub = HubClient(config)
    hub.websocket = FakeWebSocket()  # type: ignore[assignment]
    handled: list[dict[str, Any]] = []

    class FakeBridge:
        async def node_chat(self, payload: dict[str, Any]) -> None:
            handled.append(payload)

    hub.task_bridge = FakeBridge()  # type: ignore[assignment]

    await hub._handle_node_chat({"task_id": "t1"})
    await hub._handle_node_chat({"task_id": "t1", "root_task_id": "r", "text": "  \n "})
    await hub._handle_node_chat({"task_id": "t1", "root_task_id": "r", "text": "hi"})
    await hub._handle_node_chat({"task_id": "", "root_task_id": "r", "text": "hi", "reply_to": "x"})
    await _drain_chat_tasks(hub)

    assert handled == []
    assert sent_events(hub) == []


async def test_hub_node_chat_without_bridge_sends_error(config: AgentConfig) -> None:
    """桥接未注入时续聊指令应回 error(bridge_not_ready)，避免中枢挂起等待。"""
    hub = HubClient(config)
    hub.websocket = FakeWebSocket()  # type: ignore[assignment]
    hub.node_id = "node-1"

    await hub._handle_node_chat(make_chat_payload())

    event, payload = sent_events(hub)[-1]
    assert event == "agent.node_chat_error"
    assert payload == {
        "reply_to": "reply-1",
        "task_id": "t-last",
        "node_id": "node-1",
        "reason": "bridge_not_ready",
    }


async def test_hub_node_chat_crash_reports_error(config: AgentConfig) -> None:
    """bridge.node_chat 未预期崩溃时兜底回 error，避免中枢永久等待。"""
    hub = HubClient(config)
    hub.websocket = FakeWebSocket()  # type: ignore[assignment]
    hub.node_id = "node-1"

    class BoomBridge:
        async def node_chat(self, payload: dict[str, Any]) -> None:
            raise RuntimeError("boom")

    hub.task_bridge = BoomBridge()  # type: ignore[assignment]

    await hub._handle_node_chat(make_chat_payload())
    await _drain_chat_tasks(hub)

    event, payload = sent_events(hub)[-1]
    assert event == "agent.node_chat_error"
    assert payload["reply_to"] == "reply-1"
    assert payload["task_id"] == "t-last"
    assert payload["node_id"] == "node-1"
    assert payload["reason"].startswith("node_chat_internal_error:")


async def test_hub_send_node_chat_reply_payload(config: AgentConfig) -> None:
    """上行回复事件 payload 与中枢 schema 对齐；tokens 缺省时不携带该键。"""
    hub = HubClient(config)
    hub.websocket = FakeWebSocket()  # type: ignore[assignment]
    hub.node_id = "node-1"

    await hub.send_node_chat_reply(
        reply_to="reply-1",
        task_id="t-last",
        root_task_id="root",
        text="回复全文",
        tokens={"input": 10, "output": 2},
    )
    await hub.send_node_chat_reply(
        reply_to="reply-2", task_id="t-last", root_task_id="root", text="plain"
    )

    event, payload = sent_events(hub)[-2]
    assert event == "agent.node_chat_reply"
    assert payload == {
        "reply_to": "reply-1",
        "task_id": "t-last",
        "root_task_id": "root",
        "node_id": "node-1",
        "text": "回复全文",
        "tokens": {"input": 10, "output": 2},
    }
    _event, payload = sent_events(hub)[-1]
    assert "tokens" not in payload


async def test_hub_send_node_chat_error_payload(config: AgentConfig) -> None:
    """上行错误事件 payload 与中枢 schema 对齐（node_id 缺省回退已注册 ID）。"""
    hub = HubClient(config)
    hub.websocket = FakeWebSocket()  # type: ignore[assignment]
    hub.node_id = "node-1"

    await hub.send_node_chat_error(reply_to="reply-1", task_id="t-last", reason="timeout")

    event, payload = sent_events(hub)[-1]
    assert event == "agent.node_chat_error"
    assert payload == {
        "reply_to": "reply-1",
        "task_id": "t-last",
        "node_id": "node-1",
        "reason": "timeout",
    }


async def test_hub_binds_node_chat_channels_on_bridge_assembly(
    config: AgentConfig,
) -> None:
    """装配桥接时应把上行回复/错误回调注入桥接层（ADR-064 M2 装配约定）。"""
    hub = HubClient(config)
    bound: dict[str, Any] = {}

    class FakeBridge:
        def bind_node_chat_channels(self, reply: Any, error: Any) -> None:
            bound["reply"] = reply
            bound["error"] = error

    hub.task_bridge = FakeBridge()  # type: ignore[assignment]

    assert bound["reply"] == hub.send_node_chat_reply
    assert bound["error"] == hub.send_node_chat_error


# ---------------------------------------------------------------------------
# 2026-10-06 修复批次：续聊无进展看门狗 + 派发 prompt 待办纪律
# ---------------------------------------------------------------------------


async def test_node_chat_watchdog_resends_after_silent_round(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """看门狗恢复路径：首轮零事件挂起 → interrupt 清场并重发一次 prompt，
    事件恢复流动后轮次正常完成并上行回复（2026-10-06 用户实测：interrupt 后
    runner 挂起整轮零事件，旧逻辑干等 300s 超时、用户得不到任何回复）。"""
    monkeypatch.setattr("app.executor.task_bridge._NODE_CHAT_WATCHDOG_SECONDS", 0.05)
    hold = Hold()
    client = FakeTilacodeClient(streams=[[hold, *chat_round_stream()]])
    callbacks, calls = make_recorders()
    store = SessionStore(tmp_path / "sessions.json")
    await _register_session(store, "root", tmp_path / "ws-root")
    bridge = TaskBridge(config, client, store, workspace, callbacks)
    client._context_messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "回复全文"}]}
    ]

    chat_task = asyncio.create_task(bridge.node_chat(make_chat_payload()))
    try:
        await _wait_until(lambda: len(client.prompts) == 1)
        # 看门狗到期（压缩为 0.05s）→ interrupt + 重发
        await _wait_until(lambda: len(client.prompts) == 2)
        hold.release()
        await asyncio.wait_for(chat_task, timeout=5.0)
    finally:
        hold.release()
        chat_task.cancel()
        await asyncio.gather(chat_task, return_exceptions=True)

    assert client.prompts == [
        ("ses_task_root", "总结一下刚才的改动", "queue"),
        ("ses_task_root", "总结一下刚才的改动", "queue"),
    ]
    assert client.interrupts == ["ses_task_root"]
    assert len(calls["node_chat_reply"]) == 1
    assert calls["node_chat_reply"][0]["text"] == "回复全文"


async def test_node_chat_watchdog_fails_fast_when_still_silent(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """看门狗快速失败路径：重发后仍零事件 → session_unresponsive（不等 300s）。"""
    monkeypatch.setattr("app.executor.task_bridge._NODE_CHAT_WATCHDOG_SECONDS", 0.05)
    hold = Hold()
    client = FakeTilacodeClient(streams=[[hold]])
    callbacks, calls = make_recorders()
    store = SessionStore(tmp_path / "sessions.json")
    await _register_session(store, "root", tmp_path / "ws-root")
    bridge = TaskBridge(config, client, store, workspace, callbacks)

    chat_task = asyncio.create_task(bridge.node_chat(make_chat_payload()))
    try:
        await _wait_until(lambda: len(client.prompts) == 2)
        await asyncio.wait_for(chat_task, timeout=5.0)
    finally:
        hold.release()
        chat_task.cancel()
        await asyncio.gather(chat_task, return_exceptions=True)

    assert client.prompts == [
        ("ses_task_root", "总结一下刚才的改动", "queue"),
        ("ses_task_root", "总结一下刚才的改动", "queue"),
    ]
    # 重发前清场一次 + 快速失败收尾清场一次
    assert client.interrupts == ["ses_task_root", "ses_task_root"]
    assert calls["node_chat_reply"] == []
    assert len(calls["node_chat_error"]) == 1
    assert calls["node_chat_error"][0]["reason"] == "node_chat_failed: session_unresponsive"
    # 失败后并发保护随 finally 释放
    assert bridge._node_chat_sessions == set()


async def test_build_prompt_contains_todo_discipline(
    tmp_path: Path,
    config: AgentConfig,
    workspace: WorkspaceManager,
) -> None:
    """派发 prompt 必含 todo 纪律（2026-10-06 修复：子 Agent 从不上报待办，
    前端「子 Agent 待办」恒空——要求开工前用 todo 工具登记并维护计划）。"""
    client = FakeTilacodeClient()
    callbacks, _calls = make_recorders()
    store = SessionStore(tmp_path / "sessions.json")
    bridge = TaskBridge(config, client, store, workspace, callbacks)
    payload = TaskDispatchPayload.model_validate(
        {"task_id": "t1", "task_name": "n", "description": "d", "mode": "build"}
    )

    prompt = bridge._build_prompt(payload, [], tmp_path)

    assert "todo 工具" in prompt
    assert "进行中" in prompt
    assert "已完成" in prompt
