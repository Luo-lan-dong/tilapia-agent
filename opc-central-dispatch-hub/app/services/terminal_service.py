"""中枢 PTY 终端会话服务（ADR-053）。

基于 pywinpty（Windows ConPTY）实现会话级交互式终端：每个对话会话一个
PTY（PowerShell），输出经环形缓冲回放并广播给该会话的所有 WebSocket 连接；
中枢重启即销毁。AI 命令（run_command 技能）走 subprocess 双轨执行，
命令与输出以 ai_echo 事件回显到同一终端流。
"""

import asyncio
import contextlib
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

from app.core.errors import BusinessError, ErrorCode

logger = logging.getLogger(__name__)

try:  # Windows ConPTY；缺失时终端功能降级不可用（错误码 210001）
    from winpty import PtyProcess

    PTY_AVAILABLE = True
except ImportError:  # pragma: no cover - 非 Windows/未装依赖环境
    PtyProcess = None  # type: ignore[assignment]
    PTY_AVAILABLE = False

#: 输出环形缓冲上限（字符数），重连回放用
REPLAY_BUFFER_CHARS = 200_000
#: 单个订阅者队列积压上限（超出丢弃最旧，防慢连接拖垮广播）
SUB_QUEUE_MAX = 512

_SHELL_WINDOWS = "powershell.exe -NoLogo"
_SHELL_POSIX = "/bin/bash"


class TerminalSession:
    """单个 PTY 终端会话：进程 + 环形缓冲 + 订阅者广播。"""

    def __init__(self, session_id: str, cwd: str) -> None:
        self.session_id = session_id
        self.cwd = cwd
        self.buffer: deque[str] = deque()
        self.buffer_len = 0
        self.subscribers: set[asyncio.Queue[str]] = set()
        self.last_active = time.monotonic()
        self.closed = False
        self.proc: Any = None
        self._lock = asyncio.Lock()
        # 当前 PTY 尺寸（rows, cols）；resize 时更新，kickstart 重绘用
        self.rows = 30
        self.cols = 120

    def push_output(self, data: str) -> None:
        """追加输出到环形缓冲并广播给所有订阅者。"""
        self.buffer.append(data)
        self.buffer_len += len(data)
        while self.buffer_len > REPLAY_BUFFER_CHARS and self.buffer:
            self.buffer_len -= len(self.buffer[0])
            self.buffer.popleft()
        for queue in list(self.subscribers):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(data)

    def replay(self) -> str:
        """拼接环形缓冲（重连回放）。"""
        return "".join(self.buffer)

    def terminate(self) -> None:
        """终止 PTY 进程并标记关闭。"""
        self.closed = True
        if self.proc is not None:
            with contextlib.suppress(Exception):
                self.proc.terminate(force=True)


class TerminalService:
    """PTY 终端会话注册表（进程内单例）。"""

    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = asyncio.Lock()

    def default_cwd(self) -> str:
        """终端默认工作目录：中枢项目根。"""
        return str(Path.cwd())

    def get_session(self, session_id: str) -> TerminalSession | None:
        return self._sessions.get(session_id)

    async def get_or_create(self, session_id: str, cwd: str) -> TerminalSession:
        """获取或懒创建 PTY 会话；cwd 仅在首次创建时生效。"""
        async with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None and not existing.closed:
                existing.last_active = time.monotonic()
                return existing
            if not PTY_AVAILABLE:
                raise BusinessError(
                    status_code=503,
                    biz_code=ErrorCode.TERMINAL_PTY_UNAVAILABLE,
                    detail="终端不可用：缺少 pywinpty（Windows ConPTY）依赖",
                )
            if not os.path.isdir(cwd):
                cwd = self.default_cwd()
            shell = _SHELL_WINDOWS if os.name == "nt" else _SHELL_POSIX
            session = TerminalSession(session_id, cwd)
            try:
                session.proc = PtyProcess.spawn(shell, cwd=cwd, dimensions=(30, 120))
            except Exception as exc:  # noqa: BLE001 - ConPTY 创建失败
                logger.error(
                    "terminal_pty_spawn_failed",
                    extra={"event": "terminal_pty_spawn_failed", "error": str(exc)},
                )
                raise BusinessError(
                    status_code=500,
                    biz_code=ErrorCode.TERMINAL_PTY_SPAWN_FAILED,
                    detail=f"终端创建失败：{exc}",
                ) from exc
            self._sessions[session_id] = session
            asyncio.create_task(self._read_loop(session))
            asyncio.create_task(self._kickstart_repaint(session))
            logger.info(
                "terminal_session_created",
                extra={"event": "terminal_session_created", "session_id": session_id, "cwd": cwd},
            )
            return session

    async def _kickstart_repaint(self, session: TerminalSession) -> None:
        """创建后延迟注入一次同尺寸 resize，触发 ConPTY 首屏重绘。

        PowerShell/PSReadLine 在 ConPTY 下启动后不主动渲染提示符，需等到
        第一次输入或 resize 事件（2026-09-28 排查：连接成功后必须敲回车
        才能看到 prompt 即此因）。延迟须覆盖 PowerShell 启动耗时（约 1s）。
        """
        await asyncio.sleep(1.5)
        if session.closed or session.proc is None:
            return
        # 重绘触发失败不影响功能（BLE001 场景改由 suppress 收口）
        with contextlib.suppress(Exception):
            session.proc.set_size(session.cols, session.rows)

    async def _read_loop(self, session: TerminalSession) -> None:
        """后台线程读取 PTY 输出并广播（阻塞 read 放线程池）。"""
        loop = asyncio.get_running_loop()
        while not session.closed:
            try:
                data = await loop.run_in_executor(None, session.proc.read, 4096)
            except Exception:  # noqa: BLE001 - 进程退出/句柄失效
                break
            if not data:
                break
            session.push_output(data)
        session.closed = True
        session.push_output("\r\n\x1b[90m[终端会话已结束]\x1b[0m\r\n")
        logger.info(
            "terminal_session_exited",
            extra={"event": "terminal_session_exited", "session_id": session.session_id},
        )

    def write_input(self, session_id: str, data: str) -> bool:
        """向 PTY 写入用户键入。"""
        session = self._sessions.get(session_id)
        if session is None or session.closed or session.proc is None:
            return False
        session.last_active = time.monotonic()
        session.proc.write(data)
        return True

    def resize(self, session_id: str, cols: int, rows: int) -> bool:
        """调整 PTY 尺寸（cols×rows）。"""
        session = self._sessions.get(session_id)
        if session is None or session.closed or session.proc is None:
            return False
        try:
            session.cols = cols
            session.rows = rows
            session.proc.set_size(cols, rows)
            return True
        except Exception:  # noqa: BLE001 - 尺寸非法等
            return False

    def ai_echo(self, session_id: str, command: str, output: str, exit_code: int | None) -> None:
        """AI 执行回显：命令行 + 输出插入终端流（前端按 ai_echo 样式渲染）。"""
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            return
        header = f"\r\n\x1b[36m[AI 执行] $\x1b[0m {command}\r\n"
        session.push_output(header)
        if output:
            session.push_output(output.replace("\n", "\r\n") + "\r\n")
        session.push_output(f"\x1b[90m[退出码 {exit_code}]\x1b[0m\r\n")

    def close_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            session.terminate()


terminal_service = TerminalService()
