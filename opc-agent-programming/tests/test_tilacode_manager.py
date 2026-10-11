"""TilacodeManager 子进程管理与自愈测试（ADR-055 P2-11）。

覆盖：stderr 消费、monitor 异常韧性、重启退避与次数上限、不可用状态、
周期性健康检查触发重启、动态端口/密码、主动停止不误判异常。
"""

import asyncio
from pathlib import Path

import pytest

from app.config import AgentConfig
from app.executor import tilacode_manager as tm_module
from app.executor.tilacode_manager import TilacodeManager, _is_config_error_line


class FakeStream:
    """按行回放的伪子进程输出流。"""

    def __init__(self, lines: list[bytes], *, block_after: bool = False) -> None:
        self._lines = list(lines)
        self._block_after = block_after
        self.read_count = 0

    async def readline(self) -> bytes:
        """返回下一行；耗尽后阻塞（模拟长连接）或返回 EOF。"""
        self.read_count += 1
        if self._lines:
            return self._lines.pop(0)
        if self._block_after:
            await asyncio.Event().wait()
        return b""


class FakeProcess:
    """伪 asyncio 子进程：可控 returncode/wait/stdout/stderr/kill。"""

    def __init__(
        self,
        *,
        stdout_lines: list[bytes] | None = None,
        stderr_lines: list[bytes] | None = None,
        returncode: int | None = None,
    ) -> None:
        self.pid = 4242
        self.returncode = returncode
        self.stdout = FakeStream(stdout_lines or [], block_after=True)
        self.stderr = FakeStream(stderr_lines or [], block_after=True)
        self.killed = False
        self.signals: list[int] = []
        self._exit = asyncio.Event()

    def exit_with(self, code: int) -> None:
        """让进程「退出」并唤醒等待者。"""
        self.returncode = code
        self._exit.set()

    async def wait(self) -> int:
        """等待进程退出。"""
        await self._exit.wait()
        return self.returncode or 0

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        self.exit_with(0)

    def kill(self) -> None:
        self.killed = True
        self.exit_with(-9)


@pytest.fixture
def config(tmp_path: Path) -> AgentConfig:
    """测试配置：二进制指向临时文件，健康检查间隔缩短。"""
    binary = tmp_path / "tilacode"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    return AgentConfig(
        agent_registration_token="test-token",
        tilacode_binary_path=str(binary),
        tilacode_workspace_root=str(tmp_path / "ws"),
        tilacode_health_check_interval=1,
    )


@pytest.fixture
def manager(config: AgentConfig, tmp_path: Path) -> TilacodeManager:
    """构造管理器（不启动真实子进程）。"""
    return TilacodeManager(config)


#: 捕获真实 sleep（模块导入时未被 monkeypatch），供让出式替身使用——
#: 直接调用 asyncio.sleep 会在 patch 后自我递归。
_REAL_SLEEP = asyncio.sleep


async def _yielding_sleep(delay: float) -> None:
    """替代 asyncio.sleep 的「让出式」实现。

    必须是**真实**的短等待：只做零延迟让出会让被测周期循环在就绪队列里抢占
    其他任务（含测试自身的推进协程），表现为测试挂起（ADR-055 调试结论）。
    """
    await _REAL_SLEEP(0.001)


def _patch_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """把模块内 asyncio.sleep 替换为记录延迟的让出式实现。"""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        await _REAL_SLEEP(0.001)

    monkeypatch.setattr(tm_module.asyncio, "sleep", fake_sleep)
    return delays


def _patch_start(
    manager: TilacodeManager, monkeypatch: pytest.MonkeyPatch, **fake_kwargs
) -> FakeProcess:
    """把 manager.start 替换为「设置伪进程」的实现。"""
    process = FakeProcess(**fake_kwargs)

    async def fake_start() -> None:
        manager.server_password = "pwd"
        manager.actual_port = 4321
        manager._process = process  # type: ignore[assignment]

    monkeypatch.setattr(manager, "start", fake_start)
    return process


async def test_stderr_is_consumed(
    manager: TilacodeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stderr 必须被持续消费（旧实现接 PIPE 却从不读取 → 子进程写满阻塞）。"""
    manager._process = FakeProcess(stderr_lines=[b"warn: something\n", b"error: boom\n"])  # type: ignore[assignment]
    task = asyncio.create_task(manager._drain_stderr())
    await asyncio.sleep(0.05)
    assert manager._process.stderr.read_count >= 2
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_monitor_clears_dead_process_and_notifies_once(
    manager: TilacodeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """异常退出：清理死进程引用（防忙碌循环）并回调一次更新后的原因。"""
    _patch_sleep(monkeypatch)
    calls: list[str] = []

    async def fake_wait_healthy(timeout: float = 30.0, retries: int = 3) -> None:
        return None

    async def fake_start() -> None:
        manager._process = FakeProcess()  # 重启后是「活」进程

    monkeypatch.setattr(manager, "start", fake_start)
    monkeypatch.setattr(manager, "wait_healthy", fake_wait_healthy)
    # 起始为一个已退出的进程：monitor 必须摘掉它，否则下一轮又取到同一个
    # 死进程，在重启失败路径上绕过退避空转（ADR-055 修复点）
    manager._process = FakeProcess(returncode=1)  # type: ignore[assignment]

    async def on_exit(reason: str) -> None:
        calls.append(reason)
        manager._stopping = True

    task = asyncio.create_task(manager.monitor(on_unexpected_exit=on_exit))
    for _ in range(100):
        await _REAL_SLEEP(0.005)
        if task.done():
            break
    await asyncio.gather(task, return_exceptions=True)

    assert len(calls) == 1
    assert "已自动重启成功" in calls[0]
    assert manager._restart_attempts == 0


async def test_restart_backoff_doubles(
    manager: TilacodeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自动重启退避序列：1s → 2s → 4s（每次尝试翻倍，封顶 60s）。"""
    delays = _patch_sleep(monkeypatch)
    attempts = {"count": 0}

    async def fake_start() -> None:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise OSError("spawn failed")
        manager._process = FakeProcess()  # type: ignore[assignment]

    async def fake_wait_healthy(timeout: float = 30.0, retries: int = 3) -> None:
        return None

    monkeypatch.setattr(manager, "start", fake_start)
    monkeypatch.setattr(manager, "wait_healthy", fake_wait_healthy)

    first = await manager._restart_with_backoff("退出")
    second = await manager._restart_with_backoff("退出")
    third = await manager._restart_with_backoff("退出")

    assert delays == [1.0, 2.0, 4.0]
    assert "自动重启失败" in first and "自动重启失败" in second
    assert "已自动重启成功" in third
    assert manager._restart_attempts == 0  # 成功后退避计数清零


async def test_restart_gives_up_after_max_attempts(
    manager: TilacodeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连续重启失败达上限 → 标记 unavailable 且原因含「已达上限」。"""
    _patch_sleep(monkeypatch)

    async def failing_start() -> None:
        raise OSError("spawn failed")

    monkeypatch.setattr(manager, "start", failing_start)

    reasons = [
        await manager._restart_with_backoff("退出") for _ in range(tm_module._MAX_RESTART_ATTEMPTS)
    ]
    assert manager.is_unavailable is False
    assert all("自动重启失败" in reason for reason in reasons)

    final = await manager._restart_with_backoff("退出")
    assert manager.is_unavailable is True
    assert "已达上限" in final


async def test_notify_unexpected_exit_survives_callback_exception(
    manager: TilacodeManager,
) -> None:
    """回调抛异常不得向上传播（旧实现捕异常过窄，一次意外即让自愈失效）。"""
    invocations: list[int] = []

    async def on_exit(_reason: str) -> None:
        invocations.append(1)
        raise RuntimeError("callback boom")

    # 不抛出即通过
    await manager._notify_unexpected_exit(on_exit, "退出")
    assert invocations == [1]


async def test_check_health_reports_unhealthy_on_error(
    manager: TilacodeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """健康检查请求异常或非 200 时返回不健康。"""
    manager.server_password = "pwd"

    class FailingClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def get(self, _url):
            raise tm_module.httpx.ConnectError("refused")

    monkeypatch.setattr(tm_module.httpx, "AsyncClient", FailingClient)
    assert await manager.check_health() is False


async def test_health_monitor_kills_after_threshold(
    manager: TilacodeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """周期性健康检查连续失败达阈值 → 主动 kill 触发 monitor 重启。"""
    process = FakeProcess()

    async def unhealthy() -> bool:
        return False

    monkeypatch.setattr(tm_module.asyncio, "sleep", _yielding_sleep)
    monkeypatch.setattr(manager, "check_health", unhealthy)
    manager._process = process  # type: ignore[assignment]

    task = asyncio.create_task(manager.health_monitor())
    for _ in range(50):
        await asyncio.sleep(0.01)
        if process.killed:
            break
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert process.killed is True


async def test_auth_and_port_are_dynamic(
    manager: TilacodeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重启后端口/密码必须能被调用方重新取到（不得固化构造期快照）。"""
    _patch_start(manager, monkeypatch, stdout_lines=[b"listening on http://127.0.0.1:5111\n"])
    await manager.start()
    assert manager.resolved_port == 4321
    assert manager.auth() == ("tilacode", "pwd")

    # 模拟重启：端口与密码变化
    async def second_start() -> None:
        manager.server_password = "pwd2"
        manager.actual_port = 5222
        manager._process = FakeProcess()  # type: ignore[assignment]

    monkeypatch.setattr(manager, "start", second_start)
    await manager.start()
    assert manager.resolved_port == 5222
    assert manager.auth() == ("tilacode", "pwd2")


async def test_stop_does_not_look_like_crash(manager: TilacodeManager) -> None:
    """主动 stop 置 _stopping，monitor 不应把它当异常退出。"""
    process = FakeProcess()
    manager._process = process  # type: ignore[assignment]
    await manager.stop()
    assert manager._stopping is True
    assert process.signals  # 发了 SIGTERM


def test_config_error_line_detection() -> None:
    """配置非法行必须被识别（2026-09-27 事故：该错误导致任务永不执行）。"""
    # 真实 stderr 样例（含 ANSI 颜色码与「↳」前缀）
    assert _is_config_error_line(
        "\x1b[91m\x1b[1mError: \x1b[0mConfiguration is invalid at "
        "/root/.config/tilacode/tilacode.jsonc"
    )
    assert _is_config_error_line("↳ Unrecognized keys: providers, _hub_managed_providers")
    assert _is_config_error_line("Unrecognized key: providers")
    # 普通 stderr 不误判
    assert not _is_config_error_line("serving on http://127.0.0.1:4096")
    assert not _is_config_error_line("")


async def test_drain_stderr_flags_config_error(
    manager: TilacodeManager, caplog: pytest.LogCaptureFixture
) -> None:
    """stderr 中的配置非法行走 ERROR 级 tilacode_config_invalid 事件。"""
    manager._process = FakeProcess(  # type: ignore[assignment]
        stderr_lines=[
            b"serving on http://127.0.0.1:4096\n",
            b"Error: Configuration is invalid at /root/.config/tilacode/tilacode.jsonc\n",
        ]
    )
    task = asyncio.create_task(manager._drain_stderr())
    await _REAL_SLEEP(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    messages = [record.getMessage() for record in caplog.records]
    assert "tilacode_config_invalid" in messages
    assert "tilacode_stderr" in messages
