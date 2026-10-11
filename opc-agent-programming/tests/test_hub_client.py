"""HubClient 单元测试：fake WebSocket 连接，无真实网络。

覆盖心跳 busy 动态覆盖、任务派发校验/拒绝/委托执行与结果上报事件格式。
"""

import asyncio
import json
from typing import Any

import pytest

from app.client.hub_client import HubClient
from app.config import AgentConfig
from app.models.schemas import TaskDispatchPayload


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


@pytest.fixture
def config() -> AgentConfig:
    """测试配置。"""
    return AgentConfig(agent_registration_token="test-token")


@pytest.fixture
def client(config: AgentConfig, monkeypatch: pytest.MonkeyPatch) -> HubClient:
    """挂载 fake WebSocket 的客户端；心跳负载使用固定值避免受运行环境影响。"""
    hub = HubClient(config)
    hub.websocket = FakeWebSocket()  # type: ignore[assignment]
    monkeypatch.setattr(
        "app.client.hub_client.collect_metrics",
        lambda: {"load_score": {"score": 0.0}},
    )
    monkeypatch.setattr(
        "app.client.hub_client.get_resource_usage",
        lambda: {"cpu_percent": 0.0, "memory_percent": 0.0},
    )
    return hub


def sent_events(hub: HubClient) -> list[tuple[str, dict[str, Any]]]:
    """提取已发送消息的 (event, payload) 列表。"""
    ws = hub.websocket
    assert isinstance(ws, FakeWebSocket)
    return [(m["event"], m["payload"]) for m in ws.sent]


async def test_heartbeat_reports_busy_with_current_task(client: HubClient) -> None:
    """执行中心跳覆盖为 busy + 当前 task_id + running_task_count，执行外回落基础状态。"""
    running_task = asyncio.create_task(asyncio.sleep(10))
    client._execution_tasks["task-1"] = running_task
    try:
        await client._send_heartbeat()
        payload = sent_events(client)[-1][1]
        assert payload["status"] == "busy"
        assert payload["current_task_id"] == "task-1"
        assert payload["running_task_count"] == 1
        assert payload["load"] == 1.0
    finally:
        running_task.cancel()
        await asyncio.gather(running_task, return_exceptions=True)
        client._execution_tasks.clear()

    await client._send_heartbeat()
    payload = sent_events(client)[-1][1]
    assert payload["status"] == "starting"  # _node_status 默认值
    assert payload["current_task_id"] is None
    assert payload["running_task_count"] == 0
    assert payload["load"] == 0.0


async def test_task_dispatch_invalid_payload_ignored(client: HubClient) -> None:
    """缺 task_id 的派发 payload 校验失败，仅记日志不发送任何事件。"""
    await client._handle_task_dispatch({"foo": "bar"})
    assert sent_events(client) == []
    assert client._execution_tasks == {}


async def test_task_dispatch_rejected_while_busy(client: HubClient) -> None:
    """槽位满时新派发应回复 agent.task_rejected(reason=slots_full)。"""

    class FakeBridge:
        async def execute_task(self, dispatch: TaskDispatchPayload) -> None:
            pass

    client.task_bridge = FakeBridge()  # type: ignore[assignment]
    client.config.agent_max_concurrent_tasks = 1
    blocker = asyncio.Event()
    blocker_task = asyncio.create_task(blocker.wait())
    client._execution_tasks["task-1"] = blocker_task
    try:
        await client._handle_task_dispatch({"task_id": "task-2"})
        event, payload = sent_events(client)[-1]
        assert event == "agent.task_rejected"
        assert payload["task_id"] == "task-2"
        assert payload["reason"] == "slots_full"
        assert "task-1" in client._execution_tasks  # 原任务未被替换
    finally:
        blocker.set()
        blocker_task.cancel()
        await asyncio.gather(blocker_task, return_exceptions=True)
        client._execution_tasks.clear()


async def test_task_dispatch_rejected_without_bridge(config: AgentConfig) -> None:
    """桥接未注入时派发应回复 agent.task_rejected(reason=bridge_not_ready)。"""
    hub = HubClient(config)
    hub.websocket = FakeWebSocket()  # type: ignore[assignment]
    await hub._handle_task_dispatch({"task_id": "task-3"})
    event, payload = sent_events(hub)[-1]
    assert event == "agent.task_rejected"
    assert payload["task_id"] == "task-3"
    assert payload["reason"] == "bridge_not_ready"


async def test_task_dispatch_delegates_to_bridge(client: HubClient) -> None:
    """正常派发应回复 accepted 并异步委托 bridge 执行。"""
    executed: list[TaskDispatchPayload] = []

    class FakeBridge:
        async def execute_task(self, dispatch: TaskDispatchPayload) -> None:
            executed.append(dispatch)

    client.task_bridge = FakeBridge()  # type: ignore[assignment]
    await client._handle_task_dispatch(
        {"task_id": "task-4", "mode": "plan", "description": "写个脚本"}
    )

    event, payload = sent_events(client)[0]
    assert event == "agent.task_accepted"
    assert payload == {"task_id": "task-4"}

    assert "task-4" in client._execution_tasks
    task = client._execution_tasks["task-4"]
    await asyncio.wait_for(task, timeout=5)
    assert client._execution_tasks == {}  # 执行完清空（心跳回落 idle）
    assert len(executed) == 1
    assert executed[0].task_id == "task-4" and executed[0].mode == "plan"


async def test_report_callbacks_send_expected_events(client: HubClient) -> None:
    """report_* 回调应发送与中枢 payload schema 对齐的事件。"""
    await client.report_progress("task-5", 42)
    await client.report_completed("task-5", "ses_task_task-5")
    await client.report_failed("task-5", "boom")
    assert sent_events(client) == [
        ("agent.task_progress", {"task_id": "task-5", "progress": 42}),
        ("agent.task_completed", {"task_id": "task-5", "session_id": "ses_task_task-5"}),
        ("agent.task_failed", {"task_id": "task-5", "reason": "boom"}),
    ]


async def test_report_completed_with_plan_content(client: HubClient) -> None:
    """plan_content 非 None 时完成事件 payload 应携带该键。"""
    await client.report_completed("task-6", "ses_task_task-6", "## 方案全文")
    event, payload = sent_events(client)[-1]
    assert event == "agent.task_completed"
    assert payload["task_id"] == "task-6"
    assert payload["plan_content"] == "## 方案全文"


async def test_report_completed_without_plan_content_omits_key(client: HubClient) -> None:
    """plan_content 为 None（默认）时完成事件 payload 不含 plan_content 键。"""
    await client.report_completed("task-7", "ses_task_task-7")
    _event, payload = sent_events(client)[-1]
    assert payload == {"task_id": "task-7", "session_id": "ses_task_task-7"}

    await client.report_completed("task-8", "ses_task_task-8", None)
    _event, payload = sent_events(client)[-1]
    assert "plan_content" not in payload


async def test_report_completed_with_change_summary(client: HubClient) -> None:
    """change_summary 非 None 时完成事件 payload 应携带该键。"""
    await client.report_completed(
        "task-9", "ses_task_task-9", None, "# 本轮修改说明\n- 新增登录接口"
    )
    event, payload = sent_events(client)[-1]
    assert event == "agent.task_completed"
    assert payload["change_summary"] == "# 本轮修改说明\n- 新增登录接口"
    assert "plan_content" not in payload  # None 不携带


async def test_report_completed_without_change_summary_omits_key(client: HubClient) -> None:
    """change_summary 缺省为 None 时完成事件 payload 不含该键。"""
    await client.report_completed("task-10", "ses_task_task-10")
    _event, payload = sent_events(client)[-1]
    assert "change_summary" not in payload


# ---------------- 准入自评：双门槛 + 连续采样（ADR-059 D1）----------------


def _patch_eligibility(
    monkeypatch: pytest.MonkeyPatch,
    samples: list[dict[str, float | None]],
) -> HubClient:
    """按序替换准入自评的三个取数函数，并跳过真实等待。

    样本字段：``available_memory_gb`` / ``load_score`` / ``cpu_percent``
    （``load_score`` 为 None 时走 CPU 兜底分支）。
    """
    import app.client.hub_client as hub_module

    iterator = iter(samples)

    def _next() -> dict[str, float | None]:
        try:
            return next(iterator)
        except StopIteration:
            return samples[-1]

    monkeypatch.setattr(
        hub_module, "get_available_memory_gb", lambda: _next()["available_memory_gb"]
    )
    monkeypatch.setattr(hub_module, "get_load_score", lambda: _next()["load_score"])
    monkeypatch.setattr(
        hub_module,
        "get_resource_usage",
        lambda: {"cpu_percent": _next()["cpu_percent"], "memory_percent": None},
    )
    monkeypatch.setattr(hub_module.time, "sleep", lambda _seconds: None)
    return HubClient(AgentConfig(agent_registration_token="test-token"))


def test_resource_busy_false_when_both_gates_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """两门槛都达标（可用内存 >1G 且 负载 <80）→ 放行。"""
    hub = _patch_eligibility(
        monkeypatch,
        [{"available_memory_gb": 2.5, "load_score": 45.0, "cpu_percent": 20.0}],
    )

    assert hub._resource_busy() is False


def test_resource_busy_true_when_available_memory_below_1g(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """可用内存 ≤1G（即使负载很低）→ 判忙（任一不满足即生效）。"""
    hub = _patch_eligibility(
        monkeypatch,
        [{"available_memory_gb": 0.8, "load_score": 10.0, "cpu_percent": 5.0}],
    )

    assert hub._resource_busy() is True


def test_resource_busy_true_when_load_score_at_or_above_80(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """负载 ≥80（即使内存充足）→ 判忙（任一不满足即生效）。"""
    hub = _patch_eligibility(
        monkeypatch,
        [{"available_memory_gb": 8.0, "load_score": 80.0, "cpu_percent": 30.0}],
    )

    assert hub._resource_busy() is True


def test_resource_busy_boundary_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """边界：可用内存恰好 1.0G → 判忙；负载恰好 79.9 → 不因负载判忙。"""
    at_memory_boundary = _patch_eligibility(
        monkeypatch,
        [{"available_memory_gb": 1.0, "load_score": 10.0, "cpu_percent": 5.0}],
    )
    assert at_memory_boundary._resource_busy() is True

    just_below_load = _patch_eligibility(
        monkeypatch,
        [{"available_memory_gb": 2.0, "load_score": 79.9, "cpu_percent": 5.0}],
    )
    assert just_below_load._resource_busy() is False


def test_resource_busy_absolute_memory_not_percentage(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-059 核心诉求：判据是**绝对可用内存**，与内存百分比无关。

    场景：4G 机器内存占用 82.7%（仅剩 ~0.7G）→ 不可自主；
    对照：8 核机器占用 43%（剩 2.3G）→ 可自主。旧实现按百分比会把前者判「合规」。
    """
    small_machine = _patch_eligibility(
        monkeypatch,
        [{"available_memory_gb": 0.7, "load_score": 31.6, "cpu_percent": 4.3}],
    )
    assert small_machine._resource_busy() is True

    larger_machine = _patch_eligibility(
        monkeypatch,
        [{"available_memory_gb": 2.3, "load_score": 12.3, "cpu_percent": 0.0}],
    )
    assert larger_machine._resource_busy() is False


def test_resource_busy_load_score_falls_back_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_score 缺失（心跳未采集过）时用 CPU 百分比兜底，避免漏判。"""
    hub = _patch_eligibility(
        monkeypatch,
        [{"available_memory_gb": 4.0, "load_score": None, "cpu_percent": 95.0}],
    )

    assert hub._resource_busy() is True


def test_resource_busy_false_when_metrics_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """metrics 全缺失（旧节点/psutil 不可用）→ 缺省放行，不误伤。"""
    hub = _patch_eligibility(
        monkeypatch,
        [{"available_memory_gb": None, "load_score": None, "cpu_percent": None}],
    )

    assert hub._resource_busy() is False


def test_resource_busy_false_on_single_transient_spike(monkeypatch: pytest.MonkeyPatch) -> None:
    """瞬时抖动（首次采样不足、复采恢复）不得判忙——否则任务被拒后干等重派。"""
    hub = _patch_eligibility(
        monkeypatch,
        [
            {"available_memory_gb": 0.5, "load_score": 95.0, "cpu_percent": 99.0},  # 抖动
            {"available_memory_gb": 3.0, "load_score": 20.0, "cpu_percent": 5.0},  # 复采正常
        ],
    )

    assert hub._resource_busy() is False


def test_resource_busy_true_when_sustained(monkeypatch: pytest.MonkeyPatch) -> None:
    """连续两次均不满足门槛 → 判忙。"""
    hub = _patch_eligibility(
        monkeypatch,
        [
            {"available_memory_gb": 0.4, "load_score": 92.0, "cpu_percent": 90.0},
            {"available_memory_gb": 0.4, "load_score": 93.0, "cpu_percent": 91.0},
        ],
    )

    assert hub._resource_busy() is True
