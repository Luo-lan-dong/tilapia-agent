"""HubClient 断线重连机制单元测试（ADR-020）：fake WebSocket，无真实网络。

覆盖：应用内重连循环（注册失败重试 / 断线自动重连 / close 终止）、断线期间
任务生命周期事件缓冲与补报、restore_session_state 状态恢复、hub.task_cancel
取消指令处理。
"""

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.client.hub_client import HubClient
from app.config import AgentConfig


class _OpenState:
    """模拟 websockets 连接的 state 对象。"""

    name = "OPEN"


class FakeAsyncWebSocket:
    """测试用异步迭代 WebSocket：支持立即结束 / 挂起 / 下发消息三种模式。"""

    def __init__(
        self,
        messages: list[str] | None = None,
        hang: bool = False,
    ) -> None:
        """初始化假连接。

        Args:
            messages: recv 循环依次返回的消息列表。
            hang: True 时 recv 永久挂起（模拟正常长连接）。
        """
        self.state = _OpenState()
        self.sent: list[dict[str, Any]] = []
        self._messages = list(messages or [])
        self._hang = hang

    async def send(self, message: str) -> None:
        """捕获发送的消息。"""
        self.sent.append(json.loads(message))

    async def close(self) -> None:
        """关闭连接（仅更新状态标记）。"""
        self.state.name = "CLOSED"

    def __aiter__(self) -> "FakeAsyncWebSocket":
        """返回异步迭代器自身。"""
        return self

    async def __anext__(self) -> str:
        """依次返回预置消息；耗尽后按模式结束或永久挂起。"""
        if self._messages:
            return self._messages.pop(0)
        if self._hang:
            await asyncio.Event().wait()  # 永久挂起，直到协程被取消
        raise StopAsyncIteration


@pytest.fixture
def config() -> AgentConfig:
    """测试配置：重连与心跳间隔置 0，加速循环轮转。"""
    cfg = AgentConfig(agent_registration_token="test-token")
    cfg.reconnect_interval_seconds = 0
    cfg.heartbeat_interval_seconds = 0
    return cfg


def sent_events(hub: HubClient) -> list[tuple[str, dict[str, Any]]]:
    """提取当前连接已发送消息的 (event, payload) 列表。"""
    ws = hub.websocket
    assert isinstance(ws, FakeAsyncWebSocket)
    return [(m["event"], m["payload"]) for m in ws.sent]


async def _wait_until(condition: Any, timeout: float = 5.0) -> None:
    """轮询等待条件成立，超时抛 AssertionError。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while not condition():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("等待条件超时")
        await asyncio.sleep(0.02)


async def test_run_forever_reconnects_after_disconnect(
    config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首轮会话断线后应自动重新注册建连；close() 后循环退出且不再重连。"""
    hub = HubClient(config)
    registers = 0

    async def fake_register() -> None:
        """统计注册次数。"""
        nonlocal registers
        registers += 1

    monkeypatch.setattr(hub, "register", fake_register)

    # 第一条连接立即断开（空迭代），后续连接永久挂起（模拟正常长连接）
    connections = [FakeAsyncWebSocket()]

    async def fake_connect() -> None:
        """依次分发假连接。"""
        hub.websocket = connections.pop(0) if connections else FakeAsyncWebSocket(hang=True)

    monkeypatch.setattr(hub, "connect_websocket", fake_connect)

    ready_count = 0

    async def on_session_ready() -> None:
        """统计就绪回调次数。"""
        nonlocal ready_count
        ready_count += 1

    task = asyncio.create_task(hub.run_forever(on_session_ready))
    await _wait_until(lambda: registers >= 2 and ready_count >= 2)

    # close() 后循环终止，且不再发起新一轮注册
    await hub.close()
    await asyncio.wait_for(task, timeout=5)
    registers_after_close = registers
    await asyncio.sleep(0.1)
    assert registers == registers_after_close


async def test_run_forever_retries_when_register_fails(
    config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """中枢离线导致注册失败时应持续重试不退出，恢复后成功建连。"""
    hub = HubClient(config)
    attempts = 0
    connects = 0

    async def fake_register() -> None:
        """前两次模拟中枢离线（连接拒绝），之后成功。"""
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(hub, "register", fake_register)

    async def fake_connect() -> None:
        """建连成功并挂起。"""
        nonlocal connects
        connects += 1
        hub.websocket = FakeAsyncWebSocket(hang=True)

    monkeypatch.setattr(hub, "connect_websocket", fake_connect)

    task = asyncio.create_task(hub.run_forever())
    await _wait_until(lambda: attempts >= 3 and connects >= 1)

    await hub.close()
    await asyncio.wait_for(task, timeout=5)
    assert connects == 1  # 中枢恢复后仅建连一次


async def test_lifecycle_events_buffered_while_offline_and_flushed_after_reconnect(
    config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """断线期间 completed/failed 入缓冲、progress 丢弃；重连后按序补报。"""
    monkeypatch.setattr(
        "app.client.hub_client.collect_metrics",
        lambda: {"load_score": {"score": 0.0}},
    )
    hub = HubClient(config)
    hub.websocket = None  # 模拟断线

    await hub.report_completed("task-1", "ses_task_task-1")
    await hub.report_failed("task-1", "boom")
    await hub.report_progress("task-1", 50)  # 过程事件不缓冲
    assert [(e, p["task_id"]) for e, p in hub._pending_events] == [
        ("agent.task_completed", "task-1"),
        ("agent.task_failed", "task-1"),
    ]

    # 重连成功后补报 + 恢复 idle 状态
    hub.websocket = FakeAsyncWebSocket()  # type: ignore[assignment]
    await hub.restore_session_state()
    events = sent_events(hub)
    assert events[:2] == [
        ("agent.task_completed", {"task_id": "task-1", "session_id": "ses_task_task-1"}),
        ("agent.task_failed", {"task_id": "task-1", "reason": "boom"}),
    ]
    # 最后一条为恢复状态心跳：无执行中任务 → idle
    assert events[-1][0] == "agent.heartbeat"
    assert events[-1][1]["status"] == "idle"
    assert events[-1][1]["current_task_id"] is None
    assert len(hub._pending_events) == 0


async def test_restore_session_state_reports_busy_while_executing(config: AgentConfig) -> None:
    """重连时若仍有执行中任务，应上报 busy + current_task_id + running_count 而非 idle。"""
    hub = HubClient(config)
    hub.websocket = FakeAsyncWebSocket()  # type: ignore[assignment]
    hub._execution_tasks["task-2"] = asyncio.create_task(asyncio.sleep(10))

    await hub.restore_session_state()
    event, payload = sent_events(hub)[-1]
    assert event == "agent.heartbeat"
    assert payload["status"] == "busy"
    assert payload["current_task_id"] == "task-2"
    assert payload["running_task_count"] == 1


async def test_buffered_completed_event_sent_failure_requeues(
    config: AgentConfig,
) -> None:
    """补报过程中再次断线时，剩余事件应保留在缓冲队列等待下轮补报。"""
    hub = HubClient(config)
    hub.websocket = None
    await hub.report_completed("task-1", "s1")
    await hub.report_failed("task-1", "boom")

    # 先放入一条可成功发送的连接，冲刷第一条后替换为已关闭连接触发停止
    class _HalfOpenWebSocket(FakeAsyncWebSocket):
        """第一条 send 成功后进入 CLOSED 状态的连接。"""

        async def send(self, message: str) -> None:
            await super().send(message)
            self.state.name = "CLOSED"

    hub.websocket = _HalfOpenWebSocket()  # type: ignore[assignment]
    await hub._flush_pending_events()
    ws = hub.websocket
    assert isinstance(ws, _HalfOpenWebSocket)
    assert len(ws.sent) == 1  # 第一条 completed 已发出
    assert len(hub._pending_events) == 1  # 第二条 failed 未发出，留在队列


async def test_task_cancel_interrupts_current_task(config: AgentConfig) -> None:
    """hub.task_cancel 命中在跑任务时应调用 bridge.interrupt 本地中断。"""
    hub = HubClient(config)
    hub.websocket = FakeAsyncWebSocket()  # type: ignore[assignment]
    interrupted: list[str] = []

    class FakeBridge:
        """记录 interrupt 调用的假桥接。"""

        async def interrupt(self, task_id: str) -> None:
            """记录被中断的任务 ID。"""
            interrupted.append(task_id)

    hub.task_bridge = FakeBridge()  # type: ignore[assignment]
    hub._execution_tasks["task-3"] = asyncio.create_task(asyncio.sleep(10))

    await hub._handle_task_cancel({"task_id": "task-3", "reason": "已改派"})
    assert interrupted == ["task-3"]


async def test_task_cancel_ignored_for_other_task(config: AgentConfig) -> None:
    """取消指令的任务 ID 与在跑任务不符时应忽略，不触发中断。"""
    hub = HubClient(config)
    hub.websocket = FakeAsyncWebSocket()  # type: ignore[assignment]
    interrupted: list[str] = []

    class FakeBridge:
        """记录 interrupt 调用的假桥接。"""

        async def interrupt(self, task_id: str) -> None:
            """记录被中断的任务 ID。"""
            interrupted.append(task_id)

    hub.task_bridge = FakeBridge()  # type: ignore[assignment]
    hub._execution_tasks["task-4"] = asyncio.create_task(asyncio.sleep(10))

    await hub._handle_task_cancel({"task_id": "task-other", "reason": "已改派"})
    assert interrupted == []


async def test_task_cancel_without_bridge_is_ignored(config: AgentConfig) -> None:
    """桥接未注入时取消指令应安全忽略（不抛异常）。"""
    hub = HubClient(config)
    hub.websocket = FakeAsyncWebSocket()  # type: ignore[assignment]
    hub._execution_tasks["task-5"] = asyncio.create_task(asyncio.sleep(10))

    await hub._handle_task_cancel({"task_id": "task-5"})
    assert "task-5" in hub._execution_tasks


# ---------------- ADR-061 M5a-steer：中枢定向发言 ----------------
# 与 cancel 的区别：steer **不中断任务**，只把文本送进活动会话。


async def test_task_steer_sends_text_to_active_task(config: AgentConfig) -> None:
    """hub.task_steer 命中在跑任务时应调用 bridge.steer 送话（不中断任务）。"""
    hub = HubClient(config)
    hub.websocket = FakeAsyncWebSocket()  # type: ignore[assignment]
    steered: list[tuple[str, str]] = []

    class FakeBridge:
        """记录 steer 调用的假桥接。"""

        async def steer(self, task_id: str, text: str) -> bool:
            """记录被发言的任务与文本。"""
            steered.append((task_id, text))
            return True

    hub.task_bridge = FakeBridge()  # type: ignore[assignment]
    hub._execution_tasks["task-6"] = asyncio.create_task(asyncio.sleep(10))

    await hub._handle_task_steer({"task_id": "task-6", "text": "先跑测试再改代码"})
    assert steered == [("task-6", "先跑测试再改代码")]
    assert "task-6" in hub._execution_tasks  # 任务未被中断


async def test_task_steer_ignored_for_other_task(config: AgentConfig) -> None:
    """发言目标与在跑任务不符时忽略（任务可能已被改派他人）。"""
    hub = HubClient(config)
    hub.websocket = FakeAsyncWebSocket()  # type: ignore[assignment]
    steered: list[tuple[str, str]] = []

    class FakeBridge:
        """记录 steer 调用的假桥接。"""

        async def steer(self, task_id: str, text: str) -> bool:
            """记录被发言的任务与文本。"""
            steered.append((task_id, text))
            return True

    hub.task_bridge = FakeBridge()  # type: ignore[assignment]
    hub._execution_tasks["task-7"] = asyncio.create_task(asyncio.sleep(10))

    await hub._handle_task_steer({"task_id": "task-other", "text": "喂"})
    assert steered == []


async def test_task_steer_invalid_payload_is_ignored(config: AgentConfig) -> None:
    """缺 task_id / 缺 text / 纯空白文本一律安全忽略（不发话、不抛异常）。"""
    hub = HubClient(config)
    hub.websocket = FakeAsyncWebSocket()  # type: ignore[assignment]
    steered: list[tuple[str, str]] = []

    class FakeBridge:
        """记录 steer 调用的假桥接。"""

        async def steer(self, task_id: str, text: str) -> bool:
            """记录被发言的任务与文本。"""
            steered.append((task_id, text))
            return True

    hub.task_bridge = FakeBridge()  # type: ignore[assignment]
    hub._execution_tasks["task-8"] = asyncio.create_task(asyncio.sleep(10))

    await hub._handle_task_steer({"task_id": "task-8"})
    await hub._handle_task_steer({"task_id": "task-8", "text": "   \n "})
    await hub._handle_task_steer({"task_id": "", "text": "hi"})
    await hub._handle_task_steer({"text": "hi"})
    assert steered == []


async def test_task_steer_without_bridge_is_ignored(config: AgentConfig) -> None:
    """桥接未注入时发言指令安全忽略（不抛异常）。"""
    hub = HubClient(config)
    hub.websocket = FakeAsyncWebSocket()  # type: ignore[assignment]
    hub._execution_tasks["task-9"] = asyncio.create_task(asyncio.sleep(10))

    await hub._handle_task_steer({"task_id": "task-9", "text": "hi"})
    assert "task-9" in hub._execution_tasks


async def test_task_steer_failure_does_not_propagate(config: AgentConfig) -> None:
    """bridge.steer 抛异常时不得向外抛（任务照常继续，只记日志）。"""
    hub = HubClient(config)
    hub.websocket = FakeAsyncWebSocket()  # type: ignore[assignment]

    class BoomBridge:
        """steer 必失败的假桥接。"""

        async def steer(self, task_id: str, text: str) -> bool:
            """模拟送话失败。"""
            raise RuntimeError("steer boom")

    hub.task_bridge = BoomBridge()  # type: ignore[assignment]
    hub._execution_tasks["task-10"] = asyncio.create_task(asyncio.sleep(10))

    await hub._handle_task_steer({"task_id": "task-10", "text": "hi"})  # 不抛即通过
    assert "task-10" in hub._execution_tasks


# ---------------- ADR-035：并行竞速探测（prober 拆分至 endpoint_probe） ----------------


async def test_candidate_api_urls_cover_probe_range(
    config: AgentConfig,
) -> None:
    """候选地址 = 首选地址 + 同主机 8000-8010 避让范围（去重）。"""
    config.hub_api_url = "http://10.0.0.2:8000"
    hub = HubClient(config)

    candidates = hub.prober.candidate_api_urls()

    assert candidates[0] == "http://10.0.0.2:8000"
    assert "http://10.0.0.2:8001" in candidates
    assert "http://10.0.0.2:8010" in candidates
    # 首选端口在探测范围内时不重复
    assert candidates.count("http://10.0.0.2:8000") == 1
    assert len(candidates) == 11


async def test_candidate_api_urls_locked_first(
    config: AgentConfig,
) -> None:
    """锁定端点排首位，活跃地址优先使用锁定端点。"""
    config.hub_api_url = "http://10.0.0.2:8000"
    hub = HubClient(config)
    hub.prober.lock("http://10.0.0.2:8003")

    assert hub.prober.candidate_api_urls()[0] == "http://10.0.0.2:8003"
    assert hub.prober.active_api_url == "http://10.0.0.2:8003"


async def test_unlock_after_session_failure_covers_all_candidates(
    config: AgentConfig,
) -> None:
    """会话失败解锁端点后，竞速环重新覆盖全部候选（含旧锁定端口，ADR-031 修复语义保留）。

    回归场景：锁定 8000 后中枢重启换端口，解锁进入全候选并行竞速，
    任何端口（含回退到 8000）都可再次胜出，无串行轮转盲区。
    """
    config.hub_api_url = "http://10.0.0.2:8000"
    hub = HubClient(config)
    hub.prober.lock("http://10.0.0.2:8003")

    hub.prober.unlock()

    assert hub.prober.active_api_url == "http://10.0.0.2:8000"
    assert "http://10.0.0.2:8003" in hub.prober.candidate_api_urls()


async def test_register_failure_race_wins_on_other_endpoint(
    config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首选端点注册失败后，并行竞速在其他候选端点胜出并锁定。"""
    hub = HubClient(config)
    config.hub_api_url = "http://10.0.0.2:8000"
    probed_urls: list[str] = []

    async def fake_post_register(base_url: str, timeout: float) -> dict:
        """记录被探测的端点；8000 失败、8001 成功、其余失败。"""
        probed_urls.append(base_url)
        if base_url == "http://10.0.0.2:8001":
            return {"id": "node-1", "registration_token": "t"}
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(hub.prober, "_post_register", fake_post_register)

    async def fake_connect() -> None:
        """成功建连后挂起，等待 close() 终止循环。"""
        hub.websocket = FakeAsyncWebSocket(hang=True)

    monkeypatch.setattr(hub, "connect_websocket", fake_connect)

    task = asyncio.create_task(hub.run_forever())
    await _wait_until(lambda: hub.prober.active_api_url == "http://10.0.0.2:8001")
    await hub.close()
    await asyncio.wait_for(task, timeout=5)

    # 并行竞速：全部候选被同时探测（而非串行逐个）
    assert "http://10.0.0.2:8000" in probed_urls
    assert "http://10.0.0.2:8002" in probed_urls
    assert hub.prober.active_api_url == "http://10.0.0.2:8001"


# ---------------- ADR-035 第二步：中枢反连 notify 处理 ----------------


async def test_apply_hub_notify_updates_and_reconnects(
    config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """notify 新地址 → 候选首选更新 + 解锁 + 置立即重连信号。"""
    hub = HubClient(config)
    config.hub_api_url = "http://10.0.0.2:8000"
    hub.prober.lock("http://10.0.0.2:8003")
    hub.websocket = FakeAsyncWebSocket(hang=True)

    await hub.apply_hub_notify("http://10.0.0.2:8021", "ws://10.0.0.2:8021/ws/agent")

    assert config.hub_api_url == "http://10.0.0.2:8021"
    assert hub.prober.active_api_url == "http://10.0.0.2:8021"
    assert hub.prober.candidate_api_urls()[0] == "http://10.0.0.2:8021"
    assert hub._reconnect_now.is_set()
    # 当前 WS 已被主动关闭以触发会话结束
    assert hub.websocket.state.name == "CLOSED"


async def test_apply_hub_notify_ignored_for_same_endpoint(
    config: AgentConfig,
) -> None:
    """notify 地址与当前活跃端点一致时跳过（降级链路：零开销）。"""
    hub = HubClient(config)
    config.hub_api_url = "http://10.0.0.2:8000"
    hub.prober.lock("http://10.0.0.2:8003")
    hub.websocket = FakeAsyncWebSocket(hang=True)

    await hub.apply_hub_notify("http://10.0.0.2:8003")

    assert config.hub_api_url == "http://10.0.0.2:8000"  # 未被改写
    assert hub.prober.active_api_url == "http://10.0.0.2:8003"  # 锁定未解除
    assert not hub._reconnect_now.is_set()


async def test_wait_before_reconnect_interrupted_by_notify_signal(
    config: AgentConfig,
) -> None:
    """立即重连信号应在重连间隔等待期内提前打断（秒级重连）。"""
    hub = HubClient(config)
    config.reconnect_interval_seconds = 30

    async def _set_later() -> None:
        """100ms 后置位立即重连信号。"""
        await asyncio.sleep(0.1)
        hub._reconnect_now.set()

    setter = asyncio.create_task(_set_later())
    start = asyncio.get_event_loop().time()
    await hub._wait_before_reconnect()
    elapsed = asyncio.get_event_loop().time() - start

    await setter
    assert elapsed < 1  # 未等满 30s 间隔
    assert not hub._reconnect_now.is_set()  # 信号已被消费清除


async def test_register_payload_carries_reported_address(
    config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UT-5：配置 API_TOKEN 时注册 payload 携带完整 reported_address。"""
    hub = HubClient(config)
    config.api_token = "notify-token-123"
    monkeypatch.setattr("app.client.hub_client._collect_hardware_info", lambda: {"cpu_count": 4})
    monkeypatch.setattr("app.config._detect_lan_ip", lambda: "192.168.1.50")

    payload = hub._build_register_payload()

    assert payload["reported_address"] == {
        "ip": "192.168.1.50",
        "api_port": 18080,
        "api_token": "notify-token-123",
    }


async def test_register_payload_reported_address_none_without_token(
    config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧安装（无 API_TOKEN）时 reported_address 为 None，中枢跳过反连。"""
    hub = HubClient(config)
    config.api_token = None
    monkeypatch.setattr("app.config._detect_lan_ip", lambda: "192.168.1.50")

    payload = hub._build_register_payload()

    assert payload["reported_address"] is None
