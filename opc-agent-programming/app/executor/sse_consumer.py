"""tilacode Session 级 SSE 事件消费循环。

实现任务桥接层的核心消费逻辑：

- drain 排水：``after=None`` 订阅会全量重放 session 历史（tilacode 实测
  after 缺省为 -1），因此先静默跟踪 durable.seq，直到本次 prompt 的
  ``session.next.prompt.admitted`` 到达后才恢复正常处理
- 断线重连：流意外结束后以最后 durable.seq 作为 after 续传（指数退避
  1s/2s/4s 封顶 10s；after=seq 不会重放旧事件，无需再次 drain）
- 完成判定：收到 step.ended 后启动静默计时，期间无新 step.started 且
  ``is_session_active`` 复核为 False 即判定完成
- 失败判定：``session.error`` / ``session.next.step.failed``
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.executor.event_translator import (
    EventTranslator,
    ProgressUpdate,
    is_permission_event,
)
from app.executor.tilacode_client import TilacodeAPIError, TilacodeClient

# 事件类型常量
_EVENT_PROMPT_ADMITTED: str = "session.next.prompt.admitted"
_EVENT_STEP_STARTED: str = "session.next.step.started"
_EVENT_STEP_ENDED: str = "session.next.step.ended"
_EVENT_STEP_FAILED: str = "session.next.step.failed"
_EVENT_SESSION_ERROR: str = "session.error"
_EVENT_TODO_UPDATED: str = "todo.updated"

# 断线重连指数退避：1s 起步逐次翻倍，封顶 10s
_RECONNECT_BACKOFF_INITIAL_SECONDS: float = 1.0
_RECONNECT_BACKOFF_MAX_SECONDS: float = 10.0
_RECONNECT_BACKOFF_MULTIPLIER: float = 2.0

# 完成判定轮询（ADR-055 P1-6）：step.ended 后以固定间隔复核 session/active
_IDLE_POLL_INTERVAL_SECONDS: float = 1.0
_IDLE_POLL_TOTAL_SECONDS: float = 5.0


class _IdleReached(Exception):
    """内部信号：静默窗口到期，需复核会话是否已空闲（不对外抛出）。"""


# interrupt 简化语义下的失败原因（中枢 MVP 无 interrupted 终态）
INTERRUPTED_REASON: str = "interrupted"

# 进度上报回调签名：接收翻译器产出的完整进度更新（ADR-055 起含阶段/动作）
ProgressCallback = Callable[[ProgressUpdate], Awaitable[None]]


@dataclass
class ConsumeResult:
    """消费循环退出结果。

    Attributes:
        status: 终态，``completed`` 或 ``failed``。
        reason: 失败原因描述，完成时为 None。
    """

    status: str
    reason: str | None = None


class SSEConsumer:
    """单个任务的 Session 级 SSE 消费循环。

    由任务桥接层以后台任务方式驱动 ``run()``，事件流经 ``_process_event``
    分类处理后产生进度上报；完成/失败判定形成 ``ConsumeResult`` 返回。
    """

    def __init__(
        self,
        *,
        client: TilacodeClient,
        task_id: str,
        session_id: str,
        directory: str,
        translator: EventTranslator,
        idle_timeout_seconds: float,
        on_progress: ProgressCallback,
        on_trace_rows: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> None:
        """初始化消费循环。

        Args:
            client: tilacode HTTP 客户端。
            task_id: 中枢任务 ID。
            session_id: tilacode 会话 ID。
            directory: 任务工作目录绝对路径，用于 tilacode 请求级目录隔离。
            translator: 事件翻译器（内部维护单调进度）。
            idle_timeout_seconds: step.ended 后的静默判定时长（秒）。
            on_progress: 进度上报回调（progress 变化时触发）。
            on_trace_rows: 会话轨迹行回调（ADR-063 M2 旁路，同步入队；
                None 时不采集）。
        """
        self._client = client
        self._task_id = task_id
        self._session_id = session_id
        self._directory = directory
        self._translator = translator
        self._idle_timeout = idle_timeout_seconds
        self._on_progress = on_progress
        self._on_trace_rows = on_trace_rows
        self._logger = logging.getLogger(__name__)
        self._result: ConsumeResult | None = None
        # drain 状态：首次订阅（after=None）后为 True，收到 admitted 置 False
        self._draining = True
        # 断线重连游标：最近一条事件的 durable.seq
        self._last_seq: int | None = None
        # 静默计时是否已启动（收到 step.ended 且未被新 step.started 打断）
        self._idle_armed = False
        # interrupt 标志：置位后下一个 step.ended / 静默到期即按 interrupted 结束
        self._interrupted = False
        # 上次已上报进度值，用于限频（translator 单调，值不变则跳过）
        self._last_reported_progress = 0
        # 最近一次执行活动时间（单调时钟，ADR-055 P0-2）：供无进度看门狗判活
        self._last_activity_at = time.monotonic()
        # prompt 被 admitted 的时刻（None=尚未 admitted）；用于检测
        # 「admitted 后迟迟没有任何 step」的静默失效（见 _check_startup_stall）
        self._admitted_at: float | None = None
        # 是否已观察到 step.started（首次真实执行活动）
        self._step_started = False
        # 启动停滞是否已上报（避免重复置失败）
        self._startup_stall_reported = False

    def fail(self, reason: str) -> None:
        """外部强制判定失败（如 tilacode 崩溃回调），幂等。"""
        if self._result is None:
            self._result = ConsumeResult(status="failed", reason=reason)
            self._logger.info(
                "sse_consume_forced_fail",
                extra={"task_id": self._task_id, "reason": reason},
            )

    def check_startup_stall(self, threshold_seconds: float) -> bool:
        """检测「已 admitted 但迟迟没有任何 step」的静默失效并置失败。

        背景（2026-09-27 排查）：tilacode 的模型解析（``SessionRunnerModel.resolve``）
        与若干 drain 前置步骤都在**发布任何 session 事件之前**执行，失败时只记
        自己的日志、不产生事件。对节点侧表现为：进度停在 10%（session 就绪）、
        之后零事件，直到无进度看门狗 30 分钟后才判超时——排查成本极高。

        本方法把这种「启动停滞」缩短到阈值内（默认 60s）并给出可操作原因。

        Args:
            threshold_seconds: admitted 后允许的无 step 时长（秒）。

        Returns:
            True 表示本次调用刚刚判定为启动停滞失败。
        """
        if self._result is not None or self._step_started or self._startup_stall_reported:
            return False
        if self._admitted_at is None:
            return False
        if time.monotonic() - self._admitted_at < threshold_seconds:
            return False
        self._startup_stall_reported = True
        reason = (
            f"task_startup_stalled:{int(threshold_seconds)}"
            "（会话已就绪但无任何执行事件：常见原因为执行模型不可用/"
            "provider 未加载到该工作目录，请检查节点日志中 tilacode 的 drain 错误）"
        )
        self._logger.error(
            "sse_startup_stalled",
            extra={
                "task_id": self._task_id,
                "session_id": self._session_id,
                "stalled_seconds": round(time.monotonic() - self._admitted_at, 1),
            },
        )
        self.fail(reason)
        return True

    @property
    def last_activity_at(self) -> float:
        """返回最近一次执行活动的单调时钟时间（看门狗判活用）。"""
        return self._last_activity_at

    @property
    def has_result(self) -> bool:
        """返回是否已形成终态结果。"""
        return self._result is not None

    @property
    def result(self) -> ConsumeResult | None:
        """返回当前消费结果（None 表示仍在执行，ADR-055 看门狗用）。"""
        return self._result

    @property
    def translator(self) -> EventTranslator:
        """返回事件翻译器（供桥接层读取当前阶段/动作做心跳上报）。"""
        return self._translator

    def mark_interrupted(self) -> None:
        """标记任务已被请求中断。"""
        self._interrupted = True

    @property
    def session_id(self) -> str:
        """返回关联的 tilacode 会话 ID。"""
        return self._session_id

    async def handle_todo_event(self, data: dict[str, Any]) -> None:
        """处理从实例级 SSE 收到的 ``todo.updated`` 事件数据。

        Args:
            data: ``todo.updated`` 事件的 payload，形如
                ``{sessionID, todos: [...]}`` 或 ``{todos: [...]}``。
        """
        if self._result is not None:
            return
        update = self._translator.translate({"type": _EVENT_TODO_UPDATED, "data": data})
        if update is not None:
            await self._report_progress(update)

    async def run(self) -> ConsumeResult:
        """主循环：消费事件流直到形成完成/失败结果。

        ADR-055 P1-6 完成判定：收到 ``step.ended`` 后置 ``_idle_armed``，随后：

        - 静默窗口内没有新事件（``_IdleReached``），或
        - 流本身结束（``stopped``）而仍处于静默态

        两种情况都进入 **短轮询 ``session/active``**（1s 间隔，上限 5s）判定是否
        空闲；空闲即判定完成。**不再依赖「等待超时」本身作为完成信号**，只有真实
        断流（流结束且非静默态）才按 ``after=durable.seq`` 指数退避重连。
        """
        backoff = _RECONNECT_BACKOFF_INITIAL_SECONDS
        while self._result is None:
            try:
                stopped = await self._consume_one_pass()
            except _IdleReached:
                stopped = False
            if self._result is not None:
                break
            if self._idle_armed:
                # 静默态：优先判定是否已空闲完成（无论流是静默到期还是恰好结束）
                if self._interrupted:
                    self.fail(INTERRUPTED_REASON)
                    break
                if await self._await_until_idle_or_active():
                    break
                # 仍活跃：等待下一个 step.ended 重新计时，不重连
                self._idle_armed = False
                backoff = _RECONNECT_BACKOFF_INITIAL_SECONDS
                if not stopped:
                    continue
            # 流结束且非静默态（或静默态但会话仍活跃）：按游标重连
            self._idle_armed = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * _RECONNECT_BACKOFF_MULTIPLIER, _RECONNECT_BACKOFF_MAX_SECONDS)
        if self._result is None:  # 理论不可达：循环仅在结果形成后退出
            self._result = ConsumeResult(status="failed", reason="consume_loop_exited")
        return self._result

    async def _await_until_idle_or_active(self) -> bool:
        """轮询 ``session/active`` 判定会话是否已空闲（ADR-055 P1-6）。

        Returns:
            True 表示已空闲并已形成完成结果（调用方应退出主循环）；
            False 表示仍活跃（继续消费）。查询异常时保守视为活跃
            （由无进度看门狗兜底）。
        """
        deadline = time.monotonic() + _IDLE_POLL_TOTAL_SECONDS
        # 进入循环
        while True:
            if not await self._check_active_safe():
                self._finish_completed()
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(_IDLE_POLL_INTERVAL_SECONDS)

    async def _consume_one_pass(self) -> bool:
        """消费一条 SSE 流直到结束或形成结果。

        Returns:
            True 表示流已结束（或应退出主循环）；False 表示静默窗口到期
            且流仍订阅中。

        Raises:
            TilacodeAPIError: 不抛出（内部转失败结果）。
            _IdleReached: 静默窗口到期（内部信号，供 ``run`` 统一处理）。
        """
        stream = self._client.stream_session_events(self._session_id, after=self._last_seq)
        iterator = stream.__aiter__()
        while self._result is None:
            # 静默计时通过 anext 超时实现：armed 时限定等待时长，否则无限等待
            timeout = self._idle_timeout if self._idle_armed else None
            try:
                event = await asyncio.wait_for(anext(iterator), timeout=timeout)
            except StopAsyncIteration:
                return True
            except asyncio.TimeoutError:
                # 静默窗口到期：wait_for 已取消本次 anext（底层 SSE 连接随之
                # 关闭），交由 run() 复核 active 并决定完成或重连。
                raise _IdleReached from None
            except TilacodeAPIError as exc:
                # 非 2xx 响应（如 session 已被清理）：按失败结束避免无限重试
                self.fail(f"tilacode SSE 订阅失败：{exc}")
                return True
            # ADR-055 P0-2：任何事件都视为执行活动，重置看门狗计时
            self._last_activity_at = time.monotonic()
            await self._process_event(event)
        return True

    async def _process_event(self, event: dict[str, Any]) -> None:
        """分类处理单条事件：drain / 进度 / 失败 / 静默计时。"""
        event_type = event.get("type")
        seq = self._extract_seq(event)
        if seq is not None:
            self._last_seq = seq

        if self._draining:
            # 历史重放阶段：仅跟踪 durable.seq，不做任何处理与回调；
            # 收到本次 prompt 的 admitted（发送 prompt 必产生）即排水完成
            if event_type == _EVENT_PROMPT_ADMITTED:
                self._draining = False
                self._admitted_at = time.monotonic()
                self._translator.mark_session_ready()
                self._logger.info(
                    "sse_drain_finished",
                    extra={"task_id": self._task_id, "seq": seq},
                )
            return

        if is_permission_event(event):
            return  # 权限走实例级流，由桥接层权限泵兜底
        if event_type == _EVENT_SESSION_ERROR:
            self.fail(f"session 错误：{self._error_summary(event)}")
            return
        if event_type == _EVENT_STEP_FAILED:
            self.fail(f"阶段执行失败：{self._error_summary(event)}")
            return
        if event_type == _EVENT_STEP_STARTED:
            # 新阶段开始：取消静默计时继续正常消费
            self._idle_armed = False
            self._step_started = True

        # ADR-063 M2：会话轨迹采集（旁路）——在进度翻译之前分类，异常只告警
        if self._on_trace_rows is not None:
            try:
                trace_rows = self._translator.translate_trace(event)
                if trace_rows:
                    self._on_trace_rows(trace_rows)
            except Exception as exc:  # noqa: BLE001 — 轨迹旁路，绝不影响消费循环
                self._logger.warning(
                    "sse_trace_row_emit_failed",
                    extra={"task_id": self._task_id, "error": str(exc)},
                )

        update = self._translator.translate(event)

        if event_type == _EVENT_STEP_ENDED:
            if self._interrupted:
                # 简化语义：中断后按 failed(interrupted) 上报（见 TaskBridge.interrupt）
                self.fail(INTERRUPTED_REASON)
                return
            # provider turn 结束：启动静默计时（_consume_one_pass 以超时实现）
            self._idle_armed = True
        if update is not None:
            await self._report_progress(update)

    async def _report_progress(self, update: ProgressUpdate) -> None:
        """限频上报进度：仅 progress 值变化时触发回调；同时传递 todo 列表。"""
        if update.progress == self._last_reported_progress and not update.todos:
            return
        self._last_reported_progress = update.progress
        try:
            await self._on_progress(update)
        except Exception as exc:  # 上报失败不应中断消费循环
            self._logger.warning(
                "sse_progress_report_failed",
                extra={"task_id": self._task_id, "error": str(exc)},
            )

    async def _check_active_safe(self) -> bool:
        """复核会话是否仍活跃；查询异常时保守视为活跃（继续等待）。"""
        try:
            return await self._client.is_session_active(self._session_id, directory=self._directory)
        except TilacodeAPIError as exc:
            self._logger.warning(
                "sse_active_check_failed",
                extra={"task_id": self._task_id, "error": str(exc)},
            )
            return True

    def _finish_completed(self) -> None:
        """形成完成结果。"""
        progress = self._translator.mark_complete()
        self._result = ConsumeResult(status="completed")
        self._logger.info(
            "sse_consume_completed",
            extra={"task_id": self._task_id, "session_id": self._session_id, "progress": progress},
        )

    @staticmethod
    def _extract_seq(event: dict[str, Any]) -> int | None:
        """防御式提取事件游标 ``durable.seq``。"""
        durable = event.get("durable")
        if isinstance(durable, dict):
            seq = durable.get("seq")
            if isinstance(seq, int):
                return seq
        return None

    @staticmethod
    def _error_summary(event: dict[str, Any]) -> str:
        """防御式提取错误事件的摘要文本。"""
        data = event.get("data")
        data = data if isinstance(data, dict) else {}
        error = data.get("error")
        if isinstance(error, dict) and (error.get("message") or error.get("name")):
            return str(error.get("message") or error.get("name"))
        message = data.get("message") or data.get("error")
        return str(message) if message else "unknown"
