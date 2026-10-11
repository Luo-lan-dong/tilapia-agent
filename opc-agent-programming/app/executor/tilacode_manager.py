"""tilacode server 子进程管理。

负责拉起预编译的 tilacode 二进制作为 HTTP server 子进程，提供健康检查、
stdout/stderr 消费、实际监听端口解析、异常退出自动重启（指数退避 + 次数上限）
与优雅停止。

ADR-055 P2-11 修复项：
- stdout 与 stderr **都**必须被持续消费：只读 stdout 时 stderr 管道写满会
  阻塞子进程；
- ``monitor`` 对回调异常与任务取消具备韧性：单次异常不再让自愈能力永久失效；
- 自动重启带指数退避与次数上限，超限进入 ``unavailable``（回调通知桥接层
  复位就绪标志、拒收新任务）；
- 周期性健康检查落地（此前 ``tilacode_health_check_interval`` 是死配置）；
- 真实端口/密码每次启动后重新解析并暴露，调用方不得缓存构造期快照。
"""

import asyncio
import logging
import os
import re
import secrets
import signal
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from types import TracebackType

import httpx

from app.config import AgentConfig

# 匹配 tilacode serve 启动成功后输出的监听地址行，捕获实际绑定端口，例如：
# tilacode server listening on http://127.0.0.1:4096
_LISTENING_LINE_RE = re.compile(r"listening on http://[^\s]+?:(\d+)")

# 健康检查单次 HTTP 请求超时（秒）
_HEALTH_CHECK_REQUEST_TIMEOUT_SECONDS: float = 5.0
# monitor 轮询子进程的间隔（秒）
_MONITOR_POLL_INTERVAL_SECONDS: float = 1.0
# stop() 发送 SIGTERM 后等待进程退出的宽限期（秒），超时升级为 SIGKILL
_SIGTERM_GRACE_SECONDS: float = 5.0
# 自动重启退避：1s 起步逐次翻倍，封顶 60s
_RESTART_BACKOFF_INITIAL_SECONDS: float = 1.0
_RESTART_BACKOFF_MAX_SECONDS: float = 60.0
# 连续自动重启次数上限：超过后进入 unavailable，等人工介入（防重启风暴）
_MAX_RESTART_ATTEMPTS: int = 5
# 周期性健康检查连续失败阈值：达到后主动重启（进程活着但服务不可用）
_HEALTH_FAILURE_RESTART_THRESHOLD: int = 3
# stderr 单行日志最大字符数（防日志爆炸）
_STDERR_LINE_MAX_CHARS: int = 500
#: 判定「配置非法」的 stderr 特征（2026-09-27 事故：该错误导致任务永不执行）
_CONFIG_ERROR_MARKERS: tuple[str, ...] = (
    "configuration is invalid",
    "unrecognized keys",
    "unrecognized key",
)


def _is_config_error_line(text: str) -> bool:
    """判断 stderr 行是否为 tilacode 配置校验失败（大小写不敏感）。"""
    lowered = text.lower()
    return any(marker in lowered for marker in _CONFIG_ERROR_MARKERS)


class TilacodeManager:
    """管理 tilacode server 子进程的生命周期。"""

    def __init__(self, config: AgentConfig, logger: logging.Logger | None = None) -> None:
        self._config = config
        self._logger = logger or logging.getLogger(__name__)
        self._process: asyncio.subprocess.Process | None = None
        # Basic Auth 密码（start 时随机生成，用户名固定 tilacode；严禁写入日志）
        self.server_password: str | None = None
        # 从 stdout 解析出的实际监听端口；解析失败时为 None，回退配置端口
        self.actual_port: int | None = None
        # stdout/stderr 后台读取任务，持续消费避免 PIPE 缓冲区塞满
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        # 主动停止标志：stop() 时置 True，避免 monitor 误判异常退出触发重启
        self._stopping = False
        # 连续自动重启次数（成功后清零）与是否处于不可用状态
        self._restart_attempts = 0
        self._unavailable = False
        # 周期性健康检查连续失败次数
        self._health_failures = 0

    @property
    def resolved_port(self) -> int:
        """返回实际监听端口；未解析到 stdout 监听行时回退配置端口。"""
        return self.actual_port if self.actual_port is not None else self._config.tilacode_port

    @property
    def is_unavailable(self) -> bool:
        """返回子进程是否已进入不可用状态（连续重启失败超上限）。"""
        return self._unavailable

    def auth(self) -> tuple[str, str]:
        """返回当前 Basic Auth 元组（用户名固定 tilacode，密码每次启动轮换）。

        调用方必须**动态取值**，不得缓存构造期快照——tilacode 重启会换密码，
        缓存旧密码会导致全部请求 401（ADR-055 P2-11）。
        """
        return ("tilacode", self.server_password or "")

    async def start(self) -> None:
        """启动 tilacode server 子进程。

        每次启动生成随机 Basic Auth 密码，通过 TILACODE_SERVER_PASSWORD
        环境变量注入子进程；同时创建后台任务持续读取 stdout（解析实际端口）
        与 stderr（仅记录，防管道写满阻塞子进程）。
        """
        # 已有进程在运行：沿用现有处理逻辑，避免重复拉起导致端口冲突
        if self._process is not None:
            if self._process.returncode is None:
                self._logger.info(
                    "tilacode_start_skipped_already_running",
                    extra={"pid": self._process.pid},
                )
                return
            # 旧进程已退出：清理残留读取任务后重新启动
            await self._cancel_pipe_tasks()
            self._process = None
        # 检查二进制是否存在
        if not os.path.isfile(self._config.tilacode_binary_path):
            raise FileNotFoundError(f"tilacode 二进制不存在：{self._config.tilacode_binary_path}")
        # 确保工作目录存在
        os.makedirs(self._config.tilacode_workspace_root, exist_ok=True)
        # 生成随机密码注入子进程环境变量以启用 Basic Auth（用户名固定 tilacode）
        self.server_password = secrets.token_urlsafe(32)
        env = {**os.environ, "TILACODE_SERVER_PASSWORD": self.server_password}
        self._stopping = False
        self.actual_port = None
        # 启动子进程：显式绑定 127.0.0.1 与配置端口（serve 参数为 --hostname/--port）
        self._process = await asyncio.create_subprocess_exec(
            self._config.tilacode_binary_path,
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(self._config.tilacode_port),
            cwd=self._config.tilacode_workspace_root,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stdout_task = asyncio.create_task(self._read_stdout(), name="tilacode-stdout")
        self._stderr_task = asyncio.create_task(self._drain_stderr(), name="tilacode-stderr")
        self._logger.info(
            "tilacode_started",
            extra={
                "pid": self._process.pid,
                "port": self._config.tilacode_port,
                "host": self._config.tilacode_host,
            },
        )

    async def _read_stdout(self) -> None:
        """后台持续读取子进程 stdout 直到 EOF。

        解析 "tilacode server listening on http://host:port" 行获取实际绑定端口；
        同时持续消费 stdout，避免 PIPE 缓冲区塞满导致子进程写入阻塞。
        """
        process = self._process
        if process is None or process.stdout is None:
            return
        while True:
            line = await process.stdout.readline()
            if not line:
                break  # EOF：子进程已退出或关闭 stdout
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            match = _LISTENING_LINE_RE.search(text)
            if match and self.actual_port is None:
                self.actual_port = int(match.group(1))
                self._logger.info(
                    "tilacode_listening_detected",
                    extra={"actual_port": self.actual_port},
                )
        if self.actual_port is None:
            # 未解析到监听行：记录 warning，回退使用配置端口
            self._logger.warning(
                "tilacode_port_parse_failed",
                extra={"fallback_port": self._config.tilacode_port},
            )

    async def _drain_stderr(self) -> None:
        """后台持续消费子进程 stderr（ADR-055 P2-11）。

        旧实现把 stderr 接到 ``PIPE`` 却从不读取：子进程 stderr 输出写满管道
        缓冲区后会**阻塞在写调用上**，表现为「服务无响应」。此处持续读取并
        以 WARNING 记录前若干字符（不记录可能含认证信息的长行）。

        2026-09-27 事故补强：配置非法（``Configuration is invalid`` /
        ``Unrecognized keys``）会让 tilacode **启动即退出**或让整个配置文件
        失效，是「任务永远不执行」的典型根因。此类行单独以 **ERROR** 记录并
        带固定事件名，避免淹没在大量 WARNING 中。
        """
        process = self._process
        if process is None or process.stderr is None:
            return
        while True:
            line = await process.stderr.readline()
            if not line:
                break  # EOF：子进程已退出或关闭 stderr
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            snippet = text[:_STDERR_LINE_MAX_CHARS]
            if _is_config_error_line(text):
                self._logger.error(
                    "tilacode_config_invalid",
                    extra={"line": snippet, "event": "tilacode_config_invalid"},
                )
                continue
            self._logger.warning(
                "tilacode_stderr",
                extra={"line": snippet},
            )

    async def wait_healthy(self, timeout: float = 30.0, retries: int = 3) -> None:
        """等待 tilacode server 通过健康检查。

        在 timeout 时间内最多重试 retries 次，每次请求 /api/health 端点；
        每次重试间隔 ``timeout / retries``。

        Raises:
            RuntimeError: 重试耗尽仍不健康。
        """
        for attempt in range(1, retries + 1):
            if await self.check_health():
                self._health_failures = 0
                return
            self._logger.warning(
                "tilacode_health_check_retry",
                extra={"attempt": attempt, "retries": retries},
            )
            if attempt < retries:
                await asyncio.sleep(timeout / retries)
        raise RuntimeError(f"tilacode 健康检查失败，重试 {retries} 次后仍不健康")

    async def check_health(self) -> bool:
        """单次健康检查，返回是否健康（异常与超时视为不健康）。"""
        url = f"http://{self._config.tilacode_host}:{self.resolved_port}/api/health"
        # 健康检查必须携带 Basic Auth：start() 注入的 TILACODE_SERVER_PASSWORD
        # 会保护 /api/health 在内的全部端点，未带认证将得到 401 导致健康检查失败
        auth = self.auth() if self.server_password else None
        try:
            async with httpx.AsyncClient(
                timeout=_HEALTH_CHECK_REQUEST_TIMEOUT_SECONDS,
                auth=auth,
            ) as client:
                resp = await client.get(url)
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            self._logger.warning(
                "tilacode_health_check_error",
                extra={"url": url, "error": str(exc)},
            )
            return False
        if resp.status_code == HTTPStatus.OK:
            return True
        self._logger.warning(
            "tilacode_health_check_unhealthy",
            extra={"url": url, "status_code": resp.status_code},
        )
        return False

    async def monitor(
        self,
        on_unexpected_exit: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """持续监控子进程，异常退出时按配置自动重启并触发回调。

        ADR-055 P2-11：

        - 重启带指数退避（1s→2s→4s…封顶 60s）与连续次数上限，超限进入
          ``unavailable`` 并停止自动重启；
        - 单次回调异常、单次重启异常都不会终止监控循环（旧实现捕异常过窄，
          一次意外即让自愈能力永久失效）；
        - ``CancelledError`` 沿正常取消语义向上传播。
        """
        while not self._stopping:
            process = self._process
            if process is None:
                # 尚未启动或已停止，稍后重查
                await asyncio.sleep(_MONITOR_POLL_INTERVAL_SECONDS)
                continue
            if process.returncode is None:
                returncode = await process.wait()
            else:
                # 已退出的进程不要在下一轮重复 wait（否则重启失败时会立刻
                # 再次进入同一分支，形成无退避的忙碌循环）
                returncode = process.returncode
            await self._cancel_pipe_tasks()
            # 关键：摘掉已退出的进程引用。否则下一轮又取到同一个死进程，
            # 在「重启失败」路径上会绕过退避持续空转。
            self._process = None
            if self._stopping:
                # stop() 主动停止触发的退出，不视为异常
                return
            self._logger.error(
                "tilacode_unexpected_exit",
                extra={"returncode": returncode, "pid": process.pid},
            )
            reason = f"tilacode 进程异常退出（returncode={returncode}）"
            reason = await self._restart_with_backoff(reason)
            await self._notify_unexpected_exit(on_unexpected_exit, reason)

    async def _restart_with_backoff(self, reason: str) -> str:
        """按退避策略自动重启；超限则标记 unavailable 并返回更新后的原因。"""
        if not self._config.tilacode_auto_restart:
            return reason
        if self._restart_attempts >= _MAX_RESTART_ATTEMPTS:
            self._mark_unavailable()
            return f"{reason}，连续重启已达上限（{_MAX_RESTART_ATTEMPTS} 次），节点标记不可用"
        self._restart_attempts += 1
        delay = min(
            _RESTART_BACKOFF_INITIAL_SECONDS * (2 ** (self._restart_attempts - 1)),
            _RESTART_BACKOFF_MAX_SECONDS,
        )
        self._logger.warning(
            "tilacode_restart_scheduled",
            extra={"attempt": self._restart_attempts, "delay_seconds": delay},
        )
        await asyncio.sleep(delay)
        try:
            await self.start()
            await self.wait_healthy()
        except (OSError, RuntimeError) as exc:
            self._logger.error("tilacode_restart_failed", extra={"error": str(exc)})
            return f"{reason}，自动重启失败：{exc}"
        self._restart_attempts = 0
        self._unavailable = False
        self._health_failures = 0
        self._logger.info(
            "tilacode_restarted",
            extra={
                "pid": self._process.pid if self._process else None,
                "port": self.resolved_port,
            },
        )
        return f"{reason}，已自动重启成功"

    def _mark_unavailable(self) -> None:
        """标记子进程不可用（重复标记只记一次日志）。"""
        if self._unavailable:
            return
        self._unavailable = True
        self._logger.error(
            "tilacode_unavailable",
            extra={"restart_attempts": self._restart_attempts},
        )

    @staticmethod
    async def _notify_unexpected_exit(
        on_unexpected_exit: Callable[[str], Awaitable[None]] | None,
        reason: str,
    ) -> None:
        """触发异常退出回调；回调异常仅记录日志，不终止监控循环。"""
        if on_unexpected_exit is None:
            return
        try:
            await on_unexpected_exit(reason)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 回调异常不得中断监控
            logging.getLogger(__name__).error(
                "tilacode_exit_callback_failed",
                extra={"error": str(exc)},
                exc_info=True,
            )

    async def health_monitor(self) -> None:
        """周期性健康检查循环（ADR-055 P2-11）。

        消费此前形同虚设的 ``tilacode_health_check_interval``：进程存活但服务
        不可用（如 LLM 配置异常导致事件循环卡住）时，连续失败达阈值即主动重启。
        """
        interval = float(self._config.tilacode_health_check_interval)
        while not self._stopping:
            await asyncio.sleep(interval)
            if self._stopping or self._process is None or self._process.returncode is not None:
                continue
            if await self.check_health():
                self._health_failures = 0
                continue
            self._health_failures += 1
            self._logger.warning(
                "tilacode_health_check_failed",
                extra={
                    "consecutive_failures": self._health_failures,
                    "threshold": _HEALTH_FAILURE_RESTART_THRESHOLD,
                },
            )
            if self._health_failures < _HEALTH_FAILURE_RESTART_THRESHOLD:
                continue
            self._health_failures = 0
            self._logger.error("tilacode_health_restart_triggered")
            try:
                self._process.kill()
            except ProcessLookupError:
                pass

    async def stop(self) -> None:
        """优雅停止 tilacode server 子进程。"""
        # 标记主动停止，避免 monitor 误判异常退出触发重启
        self._stopping = True
        await self._cancel_pipe_tasks()
        if self._process is None:
            return
        if self._process.returncode is not None:
            self._logger.info(
                "tilacode_already_exited",
                extra={"returncode": self._process.returncode},
            )
            self._process = None
            return
        # SIGTERM
        self._process.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(self._process.wait(), timeout=_SIGTERM_GRACE_SECONDS)
            self._logger.info(
                "tilacode_stopped",
                extra={"returncode": self._process.returncode},
            )
        except asyncio.TimeoutError:
            self._logger.warning("tilacode_sigterm_timeout_escalating_to_sigkill")
            self._process.kill()
            await self._process.wait()
            self._logger.info("tilacode_killed")
        self._process = None

    async def _cancel_pipe_tasks(self) -> None:
        """取消 stdout/stderr 后台读取任务并等待其结束。"""
        for attribute in ("_stdout_task", "_stderr_task"):
            task: asyncio.Task[None] | None = getattr(self, attribute)
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            setattr(self, attribute, None)

    async def __aenter__(self) -> "TilacodeManager":
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.stop()
