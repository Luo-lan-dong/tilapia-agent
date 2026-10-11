"""Agent 节点与中枢通信客户端。

负责节点注册、WebSocket 长连接维护（断线应用内自动重连，ADR-020）、
心跳上报以及任务派发的接收与转发：任务实际执行委托给注入的 ``TaskBridge``，
进度/完成/失败通过公开的 report_* 方法回调上报。异常在边界处捕获并记录，
避免单点故障导致退出；断线期间任务生命周期事件进入缓冲队列，重连后补报。

注：本文件超过 300 行上限（见 docs/standards/coding-standards.md 4.3）。
与中枢的通信链路（注册/心跳/重连/消息分发/任务事件上报）为单一职责内的
强内聚流程，拆分会引入不必要的间接层，故按豁免场景保留并在此说明；
端点探测职责已拆出至 ``endpoint_probe.py``（ADR-035），通知 API 服务拆出
至 ``notify_server.py``（ADR-035 第二步）。
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import platform
import re
import socket
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
import websockets
from pydantic import ValidationError

from app.client.endpoint_probe import EndpointProber
from app.config import AgentConfig, _derive_ws_url
from app.env_signature import compute_environment_signature
from app.executor.task_bridge import TaskBridge
from app.models.schemas import TaskAttachmentInfo, TaskDispatchPayload, TaskRuleDocInfo
from app.utils.monitor import (
    collect_metrics,
    get_available_memory_gb,
    get_load_score,
    get_resource_usage,
)

logger = logging.getLogger(__name__)

_HEARTBEAT_EVENT = "agent.heartbeat"
_TASK_DISPATCH_EVENT = "hub.task_dispatch"
_TASK_ACCEPTED_EVENT = "agent.task_accepted"
_TASK_REJECTED_EVENT = "agent.task_rejected"
_TASK_PROGRESS_EVENT = "agent.task_progress"
_TASK_STARTED_EVENT = "agent.task_started"
_TASK_COMPLETED_EVENT = "agent.task_completed"
#: 验收阻断上报（ADR-061 M4）：红/无法验证且自修用尽 → 中枢进入「等用户决定」
_TASK_VERIFY_BLOCKED_EVENT = "agent.task_verify_blocked"
_TASK_FAILED_EVENT = "agent.task_failed"
# 中枢 → Agent 下行事件：任务取消指令（重连对账后任务已改派/终态，见 ADR-020）
_TASK_CANCEL_EVENT = "hub.task_cancel"
#: 定向发言指令（ADR-061 M5a-steer）：把中枢的一段话以 steer 方式送进活动会话；
#: 与 cancel 的区别是**不终结任务**，只影响后续行为。未知事件一律忽略（旧节点兼容）。
_TASK_STEER_EVENT = "hub.task_steer"
#: 节点续聊对话（ADR-064 M2）：下行 hub.node_chat，上行 agent.node_chat_reply/error。
#: 续聊完全绕开任务状态机：对既有会话续发消息并回传本轮 assistant 全文。
_NODE_CHAT_EVENT = "hub.node_chat"
_NODE_CHAT_REPLY_EVENT = "agent.node_chat_reply"
_NODE_CHAT_ERROR_EVENT = "agent.node_chat_error"
# 产物回传事件（ADR-028）：上行 file_tree/file_chunk/file_error，下行 file_fetch
_FILE_TREE_EVENT = "agent.file_tree"
_FILE_CHUNK_EVENT = "agent.file_chunk"
_FILE_ERROR_EVENT = "agent.file_error"
_FILE_FETCH_EVENT = "hub.file_fetch"
# 节点日志按需拉取（ADR-047）：下行 log_fetch，上行 log_chunk/log_error
_LOG_FETCH_EVENT = "hub.log_fetch"
_LOG_CHUNK_EVENT = "agent.log_chunk"
_LOG_ERROR_EVENT = "agent.log_error"
# 日志回传分块大小（字节，对齐产物分块）
_LOG_CHUNK_SIZE = 512 * 1024

# 进度事件「当前动作」描述最大长度（ADR-055；中枢侧列为 String(500)，此处更严）
_PROGRESS_MESSAGE_MAX_CHARS = 120

# 资源自评拒单采样（2026-09-28）：连续 _BUSY_SAMPLE_COUNT 次采样均超阈值才判忙，
# 两次之间等待 _BUSY_SAMPLE_INTERVAL_SECONDS，避免瞬时抖动导致任务被拒后干等重派。
_BUSY_SAMPLE_COUNT = 2
_BUSY_SAMPLE_INTERVAL_SECONDS = 0.3

# 准入自评门槛（ADR-059 D1，与中枢 dispatch_decision_service 同口径）：
#   available_memory_gb > 1.0  AND  load_score < 80
# 任一不满足即不可自主接单 → 拒单 reason=resource_busy。
# 不用内存百分比作主判据：百分比分母随机器规格变化，小内存机器会被系统性低估风险。
_MIN_AVAILABLE_MEMORY_GB = 1.0
_MAX_LOAD_SCORE = 80.0

# 断线期间需要缓冲补报的任务生命周期事件（process 类 progress 不缓冲，ADR-020）
_BUFFERED_EVENTS: frozenset[str] = frozenset(
    {_TASK_ACCEPTED_EVENT, _TASK_COMPLETED_EVENT, _TASK_FAILED_EVENT},
)
# 断线事件缓冲队列上限：防止长期离线内存膨胀（deque 超限自动丢弃最旧事件）
_PENDING_EVENTS_MAX = 100

# 执行期心跳覆盖状态与负载
_STATUS_BUSY = "busy"
_LOAD_BUSY = 1.0
_LOAD_IDLE = 0.0

_AGENT_VERSION = "0.4.2"

# 注册请求 HTTP 超时由 endpoint_probe 模块管理（锁定 30s / 竞速探测 1s，ADR-035）
# 内存换算常量：字节/MB
_BYTES_PER_MB: int = 1024 * 1024
# 非法消息日志中 raw_message 的截断长度
_RAW_MESSAGE_LOG_MAX_CHARS: int = 200

# Agent 自评拒绝原因枚举（ADR-032）
_REASON_SLOTS_FULL: str = "slots_full"
_REASON_RESOURCE_BUSY: str = "resource_busy"
_REASON_BRIDGE_NOT_READY: str = "bridge_not_ready"
_REASON_SHUTTING_DOWN: str = "shutting_down"

# 节点 Agent 会话轨迹上报（ADR-063 M1）：中枢 ingress 挂在 REST 同一 /api/v1
# 基址下（与节点注册 /api/v1/agent-nodes/register 同源，tasks 路由前缀 /tasks），
# 批量行按 (task_id, seq) 幂等去重，重复上报安全。
_AGENT_TRACE_PATH: str = "/api/v1/tasks/{task_id}/agent-trace"
# 轨迹上报 HTTP 超时（秒）：纯旁路链路，短超时避免拖慢任务主链路
_AGENT_TRACE_TIMEOUT_SECONDS: float = 5.0


def _mask_token_in_url(url: str) -> str:
    """将 URL 中的 token 查询参数脱敏。"""
    return re.sub(r"token=[^&]+", "token=***", url)


def _is_websocket_closed(
    websocket: websockets.WebSocketClientProtocol | websockets.ClientConnection | None,
) -> bool:
    """兼容 websockets 新旧版本的状态判断。"""
    if websocket is None:
        return True
    state = getattr(websocket, "state", None)
    if state is not None:
        return state.name == "CLOSED"
    return getattr(websocket, "closed", False)


def _collect_hardware_info() -> dict[str, Any]:
    """收集节点硬件信息，使用标准库，无法获取的字段填 0 或 None。"""
    memory_mb = 0
    if platform.system() == "Linux":
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            num_pages = os.sysconf("SC_PHYS_PAGES")
            memory_mb = (page_size * num_pages) // _BYTES_PER_MB
        except (ValueError, OSError):
            pass
    return {
        "cpu_count": os.cpu_count() or 1,
        "memory_mb": memory_mb,
        "gpu": None,
    }


class HubClient:
    """OPC 中枢通信客户端。"""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.node_id: str | None = None
        self.registration_token: str | None = None
        self.websocket: websockets.WebSocketClientProtocol | None = None
        self._stop_event = asyncio.Event()
        self._node_status: str = "starting"
        # 任务桥接（main 装配后注入，见 task_bridge 属性 setter）；在跑任务字典
        # task_id -> asyncio.Task
        self._task_bridge: TaskBridge | None = None
        self._execution_tasks: dict[str, asyncio.Task[None]] = {}
        # 进行中续聊轮任务集合（ADR-064 M2）：持引用防 GC，close() 时取消
        self._node_chat_tasks: set[asyncio.Task[None]] = set()
        # 断线期间缓冲的任务生命周期事件队列（重连后补报，见 ADR-020）
        self._pending_events: deque[tuple[str, dict[str, Any]]] = deque(
            maxlen=_PENDING_EVENTS_MAX,
        )
        # 端点探测（ADR-035 并行竞速 + ADR-031 候选环）：从 hub_client 拆出至
        # endpoint_probe 模块。未锁定时对全部候选并行竞速（首个成功者胜出并
        # 锁定），锁定后直接对锁定端点常规注册；会话失败解锁进入下一轮竞速。
        self.prober = EndpointProber(config, self._build_register_payload)
        # 立即重连信号（ADR-035）：notify API 收到中枢新地址时置位，
        # 跳过重连间隔等待（配合关闭当前 WS 触发会话结束）
        self._reconnect_now = asyncio.Event()

    @property
    def task_bridge(self) -> TaskBridge | None:
        """任务桥接器（main 装配后注入）。"""
        return self._task_bridge

    @task_bridge.setter
    def task_bridge(self, bridge: TaskBridge | None) -> None:
        """装配任务桥接器，并把节点轨迹上报回调挂到桥接层（ADR-063 M1）。

        轨迹上报是 HTTP 旁路（``report_agent_trace``），由本客户端持有；桥接层
        只在「派发开始 / 会话结束」两个时点回调。装配处一次性绑定，装配脚本
        无需在 ``BridgeCallbacks`` 里额外加行。

        桥接替身（测试 fake）可能未实现 ``bind_agent_trace``：此时跳过绑定，
        桥接层保持默认空实现——轨迹是旁路，缺失上报不影响任务。
        """
        self._task_bridge = bridge
        binder = getattr(bridge, "bind_agent_trace", None)
        if callable(binder):
            binder(self.report_agent_trace)
        # 节点续聊回复/错误上行回调（ADR-064 M2）：同样由装配处一次性绑定；
        # 桥接替身（测试 fake）可能未实现时跳过，桥接层保持默认空实现
        chat_binder = getattr(bridge, "bind_node_chat_channels", None)
        if callable(chat_binder):
            chat_binder(self.send_node_chat_reply, self.send_node_chat_error)

    def _build_register_payload(self) -> dict[str, Any]:
        """构造注册请求 payload（每次探测实时构造）。

        携带安装配置生成的通知地址上报（``reported_address``，ADR-035），
        供中枢落库 agent_nodes 并在重启后主动反连。
        """
        return {
            "name": self.config.resolved_name,
            "agent_type": self.config.agent_type,
            "capabilities": ["programming"],
            "client_id": self.config.resolved_client_id,
            "hardware_info": _collect_hardware_info(),
            "version": _AGENT_VERSION,
            "reported_address": self.config.reported_address,
        }

    async def register(self) -> None:
        """向中枢注册节点，保存返回的 node_id 与 registration_token。

        未锁定端点时由 EndpointProber 对全部候选并行竞速（首个成功者胜出
        并锁定，ADR-035）；已锁定时直接对锁定端点常规注册。
        """
        node_data = await self.prober.register()
        self.node_id = str(node_data.get("id"))
        self.registration_token = node_data.get("registration_token")

        logger.info(
            "agent_registered",
            extra={
                "node_id": self.node_id,
                "client_id": self.config.resolved_client_id,
                "agent_name": self.config.resolved_name,
                "agent_type": self.config.agent_type,
                "version": _AGENT_VERSION,
                "endpoint": self.prober.active_api_url,
            },
        )

    async def connect_websocket(self) -> None:
        """使用注册返回的凭据建立 WebSocket 连接。

        WS 地址由当前轮转到的中枢 API 地址派生（ADR-031 端口探测自愈），
        保证注册与 WS 建连使用同一候选端点。
        """
        if not self.node_id or not self.registration_token:
            raise RuntimeError("必须先完成注册才能连接 WebSocket")

        ws_base = _derive_ws_url(self.prober.active_api_url)
        ws_url = f"{ws_base.rstrip('/')}?node_id={self.node_id}&token={self.registration_token}"
        self.websocket = await websockets.connect(ws_url)
        logger.info(
            "websocket_connected",
            extra={"node_id": self.node_id, "ws_url": _mask_token_in_url(ws_url)},
        )

    async def run_forever(
        self,
        on_session_ready: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """维护与中枢的长连接会话，断线后自动重连，直到 close() 被调用（ADR-020）。

        每轮会话依次执行：重新注册（按 client_id 幂等，重连后原 token 仍有效）
        → 建立 WebSocket → 启动心跳与消息协程 → 回调 ``on_session_ready``
        （恢复状态上报与冲刷缓冲事件）→ 等待任一协程结束（视为连接失效）。

        断线不抛出、不退出进程：捕获所有连接类异常（注册 HTTP 失败、WebSocket
        握手失败、ConnectionClosed 及底层 OSError 等），等待
        ``reconnect_interval_seconds`` 后重试；仅 ``close()``（SIGTERM/SIGINT
        触发）可终止循环。断线检测依赖 websockets 库 keepalive（默认 ping 20s
        + pong 超时 20s），半开连接约 40 秒内可被识别。

        Args:
            on_session_ready: 连接建立后的就绪回调；首连时等待 tilacode 就绪后
                上报 idle，重连时恢复 busy/idle 状态并补报缓冲事件。
        """
        while not self._stop_event.is_set():
            heartbeat_task: asyncio.Task[None] | None = None
            message_task: asyncio.Task[None] | None = None
            try:
                # 1. 每轮会话先重新注册再建连（中枢离线时在此处失败进入重试；
                #    未锁定时并行竞速全部候选端点，ADR-035 端口探测自愈）
                await self.register()
                await self.connect_websocket()

                # 2. 启动心跳与消息协程
                heartbeat_task = asyncio.create_task(self.heartbeat_loop())
                message_task = asyncio.create_task(self.handle_messages())

                # 3. 连接就绪回调（首连等待 tilacode 就绪，重连时恢复状态）
                if on_session_ready is not None:
                    await on_session_ready()

                # 4. 任一协程结束即视为连接失效（消息循环断线返回 / 心跳异常退出）
                done, _ = await asyncio.wait(
                    {heartbeat_task, message_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if message_task in done:
                    logger.info("websocket_session_ended", extra={"node_id": self.node_id})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 广捕所有连接类异常：注册超时、握手拒绝、网络不可达等均可重试；
                # 解锁端点，下一轮对全部候选并行竞速（ADR-035 端口探测自愈）
                self.prober.unlock()
                logger.warning(
                    "websocket_session_failed",
                    extra={
                        "node_id": self.node_id,
                        # TimeoutError 等异常 str 为空，补异常类型名便于排查
                        # （2026-10-05 节点离线事故：日志里 error= 空无从定位）
                        "error": str(exc) or type(exc).__name__,
                        "retry_interval_seconds": self.config.reconnect_interval_seconds,
                        "next_endpoint": self.prober.active_api_url,
                    },
                )
            finally:
                # 5. 清理本轮会话协程与连接引用，防止跨轮协程/句柄泄漏
                for task in (heartbeat_task, message_task):
                    if task is not None and not task.done():
                        task.cancel()
                if heartbeat_task is not None and message_task is not None:
                    await asyncio.gather(heartbeat_task, message_task, return_exceptions=True)
                self.websocket = None

            if self._stop_event.is_set():
                break
            # 6. 固定间隔重试；等待期间可被 close() 或立即重连信号（notify
            #    API 触发，ADR-035）打断
            await self._wait_before_reconnect()
        logger.info("reconnect_loop_stopped", extra={"node_id": self.node_id})

    async def _wait_before_reconnect(self) -> None:
        """等待重连间隔；``close()`` 或立即重连信号可提前打断。

        立即重连信号（``_reconnect_now``）由 notify API 收到中枢新地址时
        置位，跳过间隔等待实现秒级重连（ADR-035）。
        """
        reconnect_task = asyncio.create_task(self._reconnect_now.wait())
        stop_task = asyncio.create_task(self._stop_event.wait())
        try:
            done, pending = await asyncio.wait(
                {reconnect_task, stop_task},
                timeout=self.config.reconnect_interval_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (reconnect_task, stop_task):
                task.cancel()
            await asyncio.gather(reconnect_task, stop_task, return_exceptions=True)
        self._reconnect_now.clear()

    async def restore_session_state(self) -> None:
        """连接建立/恢复后的状态同步（由重连循环的 on_session_ready 调用）。

        先补报缓冲的任务终态事件（completed/failed 可能释放中枢侧节点），
        再同步当前状态：仍有在跑任务时上报 busy + 其中一条 task_id（触发中枢
        任务归属对账，见 ADR-020），否则转 idle 允许中枢派发新任务。
        """
        await self._flush_pending_events()
        if self._execution_tasks:
            await self._send_heartbeat()
        else:
            await self.update_status("idle")

    async def heartbeat_loop(self) -> None:
        """周期性发送心跳，直到收到停止信号。"""
        while not self._stop_event.is_set():
            try:
                await self._send_heartbeat()
            except Exception:
                logger.exception("heartbeat_failed")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.config.heartbeat_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def handle_messages(self) -> None:
        """循环接收并处理 WebSocket 消息，连接断开后正常返回（ADR-020）。

        断线重试由 ``run_forever`` 重连循环负责，本方法不再抛错退出进程；
        半开连接由 websockets 库 keepalive 检测（约 40 秒内抛
        ConnectionClosed 退出 recv 循环），其余连接类异常原样上抛由
        重连循环统一处理。
        """
        if self.websocket is None:
            raise RuntimeError("WebSocket 未连接")

        try:
            async for raw_message in self.websocket:
                await self._handle_message(raw_message)
        except websockets.exceptions.ConnectionClosed:
            logger.info(
                "websocket_connection_closed",
                extra={"node_id": self.node_id},
            )

    async def apply_hub_notify(self, hub_public_url: str, hub_ws_url: str | None = None) -> None:
        """处理中枢反连通知：更新候选首选地址 + 解锁端点 + 立即重连。

        收到中枢新地址（重启换端口）后，把它设为候选环首选（写回
        ``config.hub_api_url``，``EndpointProber`` 据此生成候选环），
        清除锁定端点（避免仍指向旧端口），置立即重连信号打断重连间隔
        等待，并主动关闭当前 WS 触发会话结束以进入新一轮注册。

        Args:
            hub_public_url: 中枢当前 REST 地址（含新端口）。
            hub_ws_url: 中枢 WS 地址（可选，未用时由 REST 地址推导）。
        """
        # 地址未变化（已连接该端点）时跳过，避免无谓的会话中断（降级链路：零开销）
        if hub_public_url.rstrip("/") == self.prober.active_api_url.rstrip("/"):
            logger.info(
                "hub_notify_ignored_same_endpoint",
                extra={"event": "hub_notify_ignored_same_endpoint", "node_id": self.node_id},
            )
            return
        self.config.hub_api_url = hub_public_url
        if hub_ws_url is not None:
            self.config.hub_ws_url = hub_ws_url
        self.prober.unlock()
        logger.info(
            "hub_notify_applied",
            extra={
                "event": "hub_notify_applied",
                "hub_public_url": hub_public_url,
                "hub_ws_url": hub_ws_url,
                "node_id": self.node_id,
            },
        )
        # 关闭当前 WS 触发会话结束 → run_forever 进入下一轮（被信号打断）
        if self.websocket is not None and not _is_websocket_closed(self.websocket):
            try:
                await self.websocket.close()
            except Exception:
                pass
        self._reconnect_now.set()

    async def close(self) -> None:
        """停止心跳、取消所有执行中任务与进行中续聊轮并关闭 WebSocket。"""
        self._stop_event.set()
        if self._execution_tasks:
            for task in list(self._execution_tasks.values()):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._execution_tasks.values(), return_exceptions=True)
            self._execution_tasks.clear()
        if self._node_chat_tasks:
            for task in list(self._node_chat_tasks):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._node_chat_tasks, return_exceptions=True)
            self._node_chat_tasks.clear()
        if self.websocket is not None and not _is_websocket_closed(self.websocket):
            await self.websocket.close()
            logger.info("websocket_closed")

    async def update_status(self, status: str) -> None:
        """更新节点状态（如 ``starting``、``idle``）并立即发送一次心跳。"""
        self._node_status = status
        await self._send_heartbeat()
        logger.info("node_status_updated", extra={"node_id": self.node_id, "status": status})

    async def report_progress(
        self,
        task_id: str,
        progress: int,
        todos: list[dict[str, Any]] | None = None,
        *,
        stage: str | None = None,
        message: str | None = None,
        tool: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """向中枢上报任务进度（供 TaskBridge 回调）。

        ADR-055：``stage``/``message``/``tool``/``detail`` 为新增字段，供中枢
        任务摘要面板展示「当前动作」；旧中枢按 ``extra=ignore`` 忽略。
        """
        await self._send_task_progress(
            task_id,
            progress,
            todos,
            stage=stage,
            message=message,
            tool=tool,
            detail=detail,
        )

    async def report_task_started(
        self,
        task_id: str,
        executor_model_actual: str,
    ) -> None:
        """上报任务开始执行及实际使用的执行模型（ADR-029 增强）。

        session 创建成功后调用；中枢据此在任务详情展示真实执行模型。
        """
        await self._send_event(
            _TASK_STARTED_EVENT,
            {
                "task_id": task_id,
                "executor_model_actual": executor_model_actual,
            },
        )

    async def report_completed(
        self,
        task_id: str,
        session_id: str,
        plan_content: str | None = None,
        change_summary: str | None = None,
        project_context: str | None = None,
        *,
        verify_report: dict[str, Any] | None = None,
    ) -> None:
        """向中枢上报任务完成（供 TaskBridge 回调）。

        plan_content 仅 plan 模式任务携带（方案全文）；change_summary 仅
        build 模式任务携带（本轮修改说明），见 ADR-012 增补决策；
        project_context 为 AI 生成的项目上下文记忆（ADR-034）；
        verify_report 为完成闸的分层验收报告（ADR-061 M4，放行时携带）。
        """
        await self._send_task_completed(
            task_id, session_id, plan_content, change_summary, project_context, verify_report
        )

    async def report_verify_blocked(
        self, task_id: str, project_key: str, report: dict[str, Any]
    ) -> None:
        """上报验收阻断（ADR-061 M4）：中枢据此进入「等用户决定」态。

        中枢侧会：置任务 `verify_blocked` + 在任务对话区找用户决定 + 24h 无回应兜底 failed。
        """
        await self._send_event(
            _TASK_VERIFY_BLOCKED_EVENT,
            {
                "task_id": task_id,
                "session_id": project_key,
                "reason": str(report.get("summary") or "")[:2000],
                "report": report,
            },
        )

    async def report_failed(self, task_id: str, reason: str) -> None:
        """向中枢上报任务失败（供 TaskBridge 回调）。"""
        await self._send_task_failed(task_id, reason)

    async def report_agent_trace(
        self,
        task_id: str,
        *,
        node_id: str | None,
        session_id: str | None,
        rows: list[dict[str, Any]],
    ) -> None:
        """上报节点 Agent 会话轨迹行（ADR-063 M1，HTTP 旁路批量幂等）。

        POST ``/api/v1/tasks/{task_id}/agent-trace``，请求体
        ``{"node_id": ..., "session_id": ..., "rows": [{"seq", "kind", "payload"}]}``；
        中枢按 ``(task_id, seq)`` 幂等去重，重复上报安全（M1 只发 ``status`` 行）。

        **纯旁路链路**：任何失败仅记 warning（``agent_trace_report_failed``），
        绝不上抛异常、绝不影响任务执行；超时短（5s 以内），不拖慢任务主链路。
        ``node_id`` 调用方未知时可传 None，此处回退为本节点已注册 ID。

        Args:
            task_id: 任务 ID。
            node_id: 节点 ID（None 时用本节点已注册 ID）。
            session_id: 会话 ID（未知时传 None）。
            rows: 轨迹行列表；空列表直接跳过（不发请求）。
        """
        if not rows:
            return
        url = (
            f"{self.prober.active_api_url.rstrip('/')}"
            f"{_AGENT_TRACE_PATH.format(task_id=task_id)}"
        )
        body: dict[str, Any] = {
            "node_id": node_id if node_id is not None else self.node_id,
            "session_id": session_id,
            "rows": rows,
        }
        try:
            async with httpx.AsyncClient(timeout=_AGENT_TRACE_TIMEOUT_SECONDS) as client:
                response = await client.post(url, json=body)
                response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — 旁路上报，失败绝不影响任务
            logger.warning(
                "agent_trace_report_failed",
                extra={
                    "event": "agent_trace_report_failed",
                    "task_id": task_id,
                    "node_id": body["node_id"],
                    "row_count": len(rows),
                    "error": str(exc),
                },
            )
            return
        logger.debug(
            "agent_trace_reported",
            extra={"task_id": task_id, "node_id": body["node_id"], "row_count": len(rows)},
        )

    async def send_node_chat_reply(
        self,
        *,
        reply_to: str,
        task_id: str,
        root_task_id: str,
        text: str,
        tokens: dict[str, Any] | None = None,
        node_id: str | None = None,
    ) -> None:
        """上行节点续聊回复（ADR-064 M2）：中枢落会话消息，前端展示节点回复。

        Args:
            reply_to: 中枢下发的回复关联 ID。
            task_id: 链上最后任务 ID。
            root_task_id: 根任务 ID（会话归属）。
            text: 本轮 assistant 全文。
            tokens: 本轮用量明细（可得时携带，缺省省略该键）。
            node_id: 节点 ID；None 时回退本节点已注册 ID。
        """
        payload: dict[str, Any] = {
            "reply_to": reply_to,
            "task_id": task_id,
            "root_task_id": root_task_id,
            "node_id": node_id if node_id is not None else self.node_id,
            "text": text,
        }
        if tokens is not None:
            payload["tokens"] = tokens
        await self._send_event(_NODE_CHAT_REPLY_EVENT, payload)

    async def send_node_chat_error(
        self,
        *,
        reply_to: str,
        task_id: str | None = None,
        reason: str = "",
        node_id: str | None = None,
    ) -> None:
        """上行节点续聊错误（ADR-064 M2）：前端气泡报错（用户消息已落库不丢失）。

        Args:
            reply_to: 中枢下发的回复关联 ID。
            task_id: 链上最后任务 ID（payload 异常导致无法关联时可空）。
            reason: 失败原因（session_missing / busy / timeout 等）。
            node_id: 节点 ID；None 时回退本节点已注册 ID。
        """
        await self._send_event(
            _NODE_CHAT_ERROR_EVENT,
            {
                "reply_to": reply_to,
                "task_id": task_id,
                "node_id": node_id if node_id is not None else self.node_id,
                "reason": reason,
            },
        )

    def _local_ip_towards_hub(self) -> str | None:
        """探测本机与中枢通信的出口 IP（UDP connect 不实际发包，2026-09-24）。

        对中枢地址做 UDP connect 取路由源地址——这是与中枢实际通信的
        真实本机 IP（VPN/多网卡场景下比 8.8.8.8 探测更准确）。失败返回 None。
        """
        try:
            parsed = urlparse(self.config.hub_api_url)
            host = parsed.hostname
            if not host:
                return None
            port = parsed.port or 80
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect((host, port))
                return sock.getsockname()[0] or None
            finally:
                sock.close()
        except OSError:
            return None

    async def _send_heartbeat(self) -> None:
        """发送一次心跳事件：有在跑任务时上报 busy + running_task_count，否则基础状态。"""
        if _is_websocket_closed(self.websocket):
            return

        running_count = len(self._execution_tasks)
        executing_task_id = next(iter(self._execution_tasks), None)
        status = _STATUS_BUSY if running_count > 0 else self._node_status
        metrics = collect_metrics()
        load_score = metrics.get("load_score", {}).get("score")
        load = (
            _LOAD_BUSY
            if running_count > 0
            else (load_score if load_score is not None else _LOAD_IDLE)
        )
        message = {
            "event": _HEARTBEAT_EVENT,
            "payload": {
                "client_id": self.config.resolved_client_id,
                "status": status,
                "current_task_id": executing_task_id,
                "running_task_count": running_count,
                "load": load,
                "metrics": metrics,
                # 自报本机真实 IP（2026-09-24）：中枢 WS 连接源地址经 VPN/NAT
                # 会失真（如多个节点同显网关地址），节点自报出口 IP 最真实
                "ip": self._local_ip_towards_hub(),
                # 环境签名（ADR-061 M6）：中枢据此把「同环境踩过的坑」在派发前召回，
                # 跨机复用时不会把异环境解法喂过来；探测失败为 None（中枢保持原值）
                "environment_signature": compute_environment_signature(),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": None,
        }
        await self.websocket.send(json.dumps(message))
        logger.debug(
            "heartbeat_sent",
            extra={
                "node_id": self.node_id,
                "status": status,
                "load": load,
                "has_metrics": "load_score" in metrics,
            },
        )

    async def _handle_message(self, raw_message: str | bytes) -> None:
        """解析单条 WebSocket 消息并按事件类型分发。"""
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.warning(
                "invalid_json_message",
                extra={"raw_message": str(raw_message)[:_RAW_MESSAGE_LOG_MAX_CHARS]},
            )
            return

        event = message.get("event")
        payload = message.get("payload", {})

        if event == _TASK_DISPATCH_EVENT:
            await self._handle_task_dispatch(payload)
        elif event == _TASK_CANCEL_EVENT:
            await self._handle_task_cancel(payload)
        elif event == _TASK_STEER_EVENT:
            await self._handle_task_steer(payload)
        elif event == _NODE_CHAT_EVENT:
            await self._handle_node_chat(payload)
        elif event == _FILE_FETCH_EVENT:
            await self._handle_file_fetch(payload)
        elif event == _LOG_FETCH_EVENT:
            await self._handle_log_fetch(payload)
        else:
            logger.debug("unhandled_event", extra={"event": event, "node_id": self.node_id})

    async def _handle_file_fetch(self, payload: dict[str, Any]) -> None:
        """处理中枢产物拉取指令（ADR-028），委托任务桥接层读取并分块回传。

        桥接未就绪时直接回复 file_error，避免中枢侧挂起等待超时。
        """
        request_id = payload.get("request_id")
        task_id = payload.get("task_id")
        mode = payload.get("mode", "file")
        path = payload.get("path")
        if not isinstance(request_id, str) or not isinstance(task_id, str):
            logger.warning(
                "file_fetch_invalid_payload",
                extra={"node_id": self.node_id},
            )
            return
        bridge = self.task_bridge
        if bridge is None:
            await self.report_file_error(request_id, "任务桥接未就绪")
            return
        await bridge.handle_file_fetch(request_id, task_id, mode, path)

    async def _handle_log_fetch(self, payload: dict[str, Any]) -> None:
        """处理中枢日志拉取指令（ADR-047）：读本地日志文件分块回传。

        local 环境（无文件日志）或读取失败时回传 log_error，避免中枢侧
        挂起等待超时。
        """
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            logger.warning("log_fetch_invalid_payload", extra={"node_id": self.node_id})
            return
        tail_lines_raw = payload.get("tail_lines")
        try:
            tail_lines = max(1, min(20000, int(tail_lines_raw))) if tail_lines_raw else 200
        except (TypeError, ValueError):
            tail_lines = 200
        level = payload.get("level")
        level = str(level) if level and str(level).lower() != "none" else None
        try:
            # 延迟导入：log_fetcher 仅此指令使用；目录与 setup_logging 写入一致
            from app.utils.log_fetcher import read_recent_log_lines
            from app.utils.logger import resolve_log_dir

            content = read_recent_log_lines(
                tail_lines=tail_lines,
                level=level,
                log_dir=resolve_log_dir(self.config.app_env),
            )
        except Exception as exc:  # noqa: BLE001 — 读取失败回传错误
            await self.report_log_error(request_id, f"日志读取失败：{exc}")
            return
        if not content:
            await self.report_log_error(request_id, "日志文件不存在或为空")
            return
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        total = max(1, -(-len(encoded) // _LOG_CHUNK_SIZE))
        for seq in range(total):
            chunk = encoded[seq * _LOG_CHUNK_SIZE : (seq + 1) * _LOG_CHUNK_SIZE]
            await self.send_log_chunk(
                request_id,
                seq,
                total,
                chunk,
                seq == total - 1,
            )
        logger.info(
            "log_fetch_served",
            extra={
                "request_id": request_id,
                "tail_lines": tail_lines,
                "level": level,
                "bytes": len(content),
            },
        )

    async def send_log_chunk(
        self, request_id: str, seq: int, total: int, data_b64: str, done: bool
    ) -> None:
        """回传节点日志分块（ADR-047；过程类事件，断线丢弃由中枢超时兜底）。"""
        await self._send_event(
            _LOG_CHUNK_EVENT,
            {"request_id": request_id, "seq": seq, "total": total, "data": data_b64, "done": done},
        )

    async def report_log_error(self, request_id: str, reason: str) -> None:
        """上报节点日志拉取失败（ADR-047）。"""
        await self._send_event(_LOG_ERROR_EVENT, {"request_id": request_id, "reason": reason})

    async def _handle_task_cancel(self, payload: dict[str, Any]) -> None:
        """处理中枢任务取消指令：中断本地执行（重连对账后任务已改派/终态）。

        中断后 TaskBridge 按 ``failed("interrupted")`` 上报；若任务已改派
        他人，中枢归属校验会安全拒绝该上报，不影响新归属节点执行。

        Args:
            payload: 取消事件负载，含 ``task_id`` 与 ``reason``。
        """
        task_id = payload.get("task_id")
        reason = payload.get("reason", "任务归属已变更")
        if not isinstance(task_id, str) or not task_id:
            logger.warning(
                "task_cancel_invalid_payload",
                extra={"node_id": self.node_id},
            )
            return
        if task_id not in self._execution_tasks or self.task_bridge is None:
            logger.info(
                "task_cancel_ignored",
                extra={"task_id": task_id, "node_id": self.node_id, "reason": reason},
            )
            return
        logger.warning(
            "task_cancel_received",
            extra={"task_id": task_id, "node_id": self.node_id, "reason": reason},
        )
        try:
            await self.task_bridge.interrupt(task_id)
        except Exception:
            # 中断失败不致命：SSE 消费循环仍会在静默复核后结束当前任务
            logger.exception(
                "task_cancel_interrupt_failed",
                extra={"task_id": task_id, "node_id": self.node_id},
            )

    async def _handle_task_steer(self, payload: dict[str, Any]) -> None:
        """处理中枢定向发言指令：把文本以 steer 送进该任务的活动会话（ADR-061 M5a-steer）。

        与 ``_handle_task_cancel`` 的区别：**不中断任务、不改变任务状态**，只把这段话
        追加进会话，影响后续行为。任务不在本节点执行时静默忽略（可能已被改派）。

        Args:
            payload: 发言事件负载，含 ``task_id`` 与 ``text``。
        """
        task_id = payload.get("task_id")
        text = payload.get("text")
        if (
            not isinstance(task_id, str)
            or not task_id
            or not isinstance(text, str)
            or not text.strip()
        ):
            logger.warning(
                "task_steer_invalid_payload",
                extra={"node_id": self.node_id},
            )
            return
        if task_id not in self._execution_tasks or self.task_bridge is None:
            logger.info(
                "task_steer_ignored",
                extra={"task_id": task_id, "node_id": self.node_id},
            )
            return
        logger.info(
            "task_steer_received",
            extra={"task_id": task_id, "node_id": self.node_id, "text_length": len(text)},
        )
        try:
            await self.task_bridge.steer(task_id, text)
        except Exception:
            # 发言失败不致命：任务照常继续，只记日志（不伪造成功）
            logger.exception(
                "task_steer_failed",
                extra={"task_id": task_id, "node_id": self.node_id},
            )

    async def _handle_node_chat(self, payload: dict[str, Any]) -> None:
        """处理中枢节点续聊指令（ADR-064 M2）：对既有会话续发消息并回传回复。

        与 ``_handle_task_steer`` 的区别：续聊**完全绕开任务状态机**（不要求
        任务在跑、不触碰 ``_execution_tasks``），以独立任务异步执行、不阻塞
        消息接收循环；桥接未就绪时直接回 error，避免中枢侧挂起等待。

        Args:
            payload: 续聊事件负载（契约见 ADR-064 事件表）。
        """
        task_id = payload.get("task_id")
        root_task_id = payload.get("root_task_id")
        text = payload.get("text")
        reply_to = payload.get("reply_to")
        if (
            not isinstance(task_id, str)
            or not task_id
            or not isinstance(root_task_id, str)
            or not root_task_id
            or not isinstance(text, str)
            or not text.strip()
            or not isinstance(reply_to, str)
            or not reply_to
        ):
            logger.warning("node_chat_invalid_payload", extra={"node_id": self.node_id})
            return
        if self.task_bridge is None:
            logger.error("node_chat_bridge_not_ready", extra={"task_id": task_id})
            await self.send_node_chat_error(
                reply_to=reply_to, task_id=task_id, reason="bridge_not_ready"
            )
            return
        logger.info(
            "node_chat_received",
            extra={
                "task_id": task_id,
                "root_task_id": root_task_id,
                "node_id": self.node_id,
                "reply_to": reply_to,
                "text_length": len(text),
            },
        )
        chat_task = asyncio.create_task(
            self._run_node_chat(payload), name=f"node-chat-{reply_to}"
        )
        self._node_chat_tasks.add(chat_task)
        chat_task.add_done_callback(self._node_chat_tasks.discard)

    async def _run_node_chat(self, payload: dict[str, Any]) -> None:
        """执行一轮续聊并兜底未预期异常（桥接层自身负责错误回传）。"""
        bridge = self.task_bridge
        if bridge is None:
            return
        try:
            await bridge.node_chat(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 兜底必须捕获全部异常类型
            logger.error(
                "node_chat_crashed",
                extra={
                    "event": "node_chat_crashed",
                    "task_id": payload.get("task_id"),
                    "node_id": self.node_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                exc_info=True,
            )
            reply_to = payload.get("reply_to")
            if isinstance(reply_to, str) and reply_to:
                task_id = payload.get("task_id")
                await self.send_node_chat_error(
                    reply_to=reply_to,
                    task_id=task_id if isinstance(task_id, str) else None,
                    reason=f"node_chat_internal_error: {type(exc).__name__}: {exc}",
                )

    async def _handle_task_dispatch(self, payload: dict[str, Any]) -> None:
        """校验并接受任务派发，委托 TaskBridge 异步执行；桥接未就绪或
        已有任务执行中时回复 ``agent.task_rejected``。"""
        try:
            dispatch = TaskDispatchPayload.model_validate(payload)
        except ValidationError as exc:
            logger.warning(
                "task_dispatch_invalid_payload",
                extra={"node_id": self.node_id, "error": str(exc)},
            )
            return

        # 现场迁移（ADR-034）：先下载产物包再接单（失败降级为无现场继续执行）
        dispatch = await self._download_handoff_bundle(dispatch)
        # 任务附件（ADR-039）：下载附件原始字节（失败降级为无附件继续执行）
        dispatch = await self._download_task_attachments(dispatch)
        # 规则文档（ADR-048）：下载文档原始字节（失败降级为无规则继续执行）
        dispatch = await self._download_rule_docs(dispatch)

        task_id = dispatch.task_id
        logger.info("task_received", extra={"task_id": task_id, "node_id": self.node_id})

        if self._stop_event.is_set():
            await self._send_task_rejected(task_id, _REASON_SHUTTING_DOWN, message="节点正在关闭")
            return
        if self.task_bridge is None:
            logger.error("task_bridge_not_ready", extra={"task_id": task_id})
            await self._send_task_rejected(
                task_id, _REASON_BRIDGE_NOT_READY, message="任务桥接未就绪"
            )
            return
        if len(self._execution_tasks) >= self.config.agent_max_concurrent_tasks:
            logger.warning(
                "task_dispatch_rejected_slots_full",
                extra={
                    "task_id": task_id,
                    "running_count": len(self._execution_tasks),
                    "max_concurrent_tasks": self.config.agent_max_concurrent_tasks,
                },
            )
            await self._send_task_rejected(task_id, _REASON_SLOTS_FULL)
            return
        if self._resource_busy():
            # 带上真实读数（ADR-059 D3）：此前只记 task_id，现场无法判断是
            # 可用内存不足还是负载过高触发拒单
            logger.warning(
                "task_dispatch_rejected_resource_busy",
                extra={
                    "task_id": task_id,
                    "node_id": self.node_id,
                    "available_memory_gb": get_available_memory_gb(),
                    "load_score": get_load_score(),
                    "min_available_memory_gb": _MIN_AVAILABLE_MEMORY_GB,
                    "max_load_score": _MAX_LOAD_SCORE,
                },
            )
            await self._send_task_rejected(task_id, _REASON_RESOURCE_BUSY)
            return

        await self._send_task_accepted(task_id)
        self._execution_tasks[task_id] = asyncio.create_task(self._run_task(dispatch))

    async def _download_handoff_bundle(self, dispatch: TaskDispatchPayload) -> TaskDispatchPayload:
        """下载现场迁移产物包并附到 payload（ADR-034）。

        以当前连接的中枢 API 地址拼接 bundle_path；失败重试 1 次，仍失败则
        清空 handoff 信息降级为无现场继续执行（行为同旧版本 Agent，不拒绝任务）。
        """
        handoff = dispatch.handoff
        if handoff is None:
            return dispatch
        url = f"{self.prober.active_api_url}{handoff.bundle_path}"
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=300.0) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    data = response.content
                if len(data) != handoff.size:
                    raise ValueError(f"bundle 大小不匹配：实际 {len(data)}，预期 {handoff.size}")
                digest = hashlib.sha256(data).hexdigest()
                if digest != handoff.sha256:
                    raise ValueError("bundle sha256 校验失败")
                dispatch.handoff_bundle = data
                logger.info(
                    "handoff_bundle_downloaded",
                    extra={
                        "event": "handoff_bundle_downloaded",
                        "task_id": dispatch.task_id,
                        "size": len(data),
                        "attempt": attempt,
                    },
                )
                return dispatch
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(
                    "handoff_bundle_download_retry",
                    extra={
                        "event": "handoff_bundle_download_retry",
                        "task_id": dispatch.task_id,
                        "attempt": attempt,
                        "error": str(exc),
                    },
                )
        logger.warning(
            "handoff_bundle_download_failed_degrade",
            extra={
                "event": "handoff_bundle_download_failed_degrade",
                "task_id": dispatch.task_id,
            },
        )
        dispatch.handoff = None
        return dispatch

    async def _download_task_attachments(
        self, dispatch: TaskDispatchPayload
    ) -> TaskDispatchPayload:
        """下载任务附件原始字节并附到 payload（ADR-039）。

        以当前连接的中枢 API 地址拼接附件 url（token 一次性，下载即焚）；
        单附件失败重试 1 次，仍失败则跳过该附件继续（不拒绝任务）。
        """
        if not dispatch.attachments:
            return dispatch
        downloaded: list[tuple[TaskAttachmentInfo, bytes]] = []
        for attachment in dispatch.attachments:
            url = f"{self.prober.active_api_url}{attachment.url}"
            for attempt in (1, 2):
                try:
                    async with httpx.AsyncClient(timeout=120.0) as client:
                        response = await client.get(url)
                        response.raise_for_status()
                    data = response.content
                    downloaded.append((attachment, data))
                    logger.info(
                        "task_attachment_downloaded",
                        extra={
                            "event": "task_attachment_downloaded",
                            "task_id": dispatch.task_id,
                            "attachment_id": attachment.id,
                            "size": len(data),
                            "attempt": attempt,
                        },
                    )
                    break
                except httpx.HTTPError as exc:
                    logger.warning(
                        "task_attachment_download_retry",
                        extra={
                            "event": "task_attachment_download_retry",
                            "task_id": dispatch.task_id,
                            "attachment_id": attachment.id,
                            "attempt": attempt,
                            "error": str(exc),
                        },
                    )
            else:
                logger.warning(
                    "task_attachment_download_failed_skip",
                    extra={
                        "event": "task_attachment_download_failed_skip",
                        "task_id": dispatch.task_id,
                        "attachment_id": attachment.id,
                    },
                )
        dispatch.attachment_blobs = downloaded
        return dispatch

    async def _download_rule_docs(self, dispatch: TaskDispatchPayload) -> TaskDispatchPayload:
        """下载规则文档原始字节并附到 payload（ADR-048）。

        以当前连接的中枢 API 地址拼接文档 url（TTL 内可重复下载）；
        sha256 校验，单文档失败重试 1 次，仍失败则跳过该文档继续
        （规范是质量约束不是执行依赖，不拒绝任务）。
        """
        if not dispatch.rule_docs:
            return dispatch
        downloaded: list[tuple[TaskRuleDocInfo, bytes]] = []
        for doc in dispatch.rule_docs:
            url = f"{self.prober.active_api_url}{doc.url}"
            for attempt in (1, 2):
                try:
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        response = await client.get(url)
                        response.raise_for_status()
                    data = response.content
                    if len(data) != doc.size:
                        raise ValueError(f"文档大小不匹配：实际 {len(data)}，预期 {doc.size}")
                    digest = hashlib.sha256(data).hexdigest()
                    if digest != doc.sha256:
                        raise ValueError("文档 sha256 校验失败")
                    downloaded.append((doc, data))
                    logger.info(
                        "rule_doc_downloaded",
                        extra={
                            "event": "rule_doc_downloaded",
                            "task_id": dispatch.task_id,
                            "doc_id": doc.id,
                            "file_name": doc.file_name,
                            "size": len(data),
                            "attempt": attempt,
                        },
                    )
                    break
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning(
                        "rule_doc_download_retry",
                        extra={
                            "event": "rule_doc_download_retry",
                            "task_id": dispatch.task_id,
                            "doc_id": doc.id,
                            "attempt": attempt,
                            "error": str(exc),
                        },
                    )
            else:
                logger.warning(
                    "rule_doc_download_failed_skip",
                    extra={
                        "event": "rule_doc_download_failed_skip",
                        "task_id": dispatch.task_id,
                        "doc_id": doc.id,
                        "file_name": doc.file_name,
                    },
                )
        dispatch.rule_doc_blobs = downloaded
        return dispatch

    def _resource_busy(self) -> bool:
        """准入自评：判断本节点是否**不可自主接单**（ADR-059 D1）。

        判据（与中枢预筛同口径，避免「中枢放行、节点拒单」错配）：

            available_memory_gb > 1.0  且  load_score < 80

        任一不满足即判忙（更保守）。**不使用内存百分比作主判据**：百分比的分母
        随机器规格变化（4G 机器 82.7% 仅剩 ~0.7G 却会「合规」）。`load_score`
        复用心跳已算出的综合负载（已平滑）；可用内存取 psutil 的绝对可用量
        （内核直接给出，天然平滑）。

        仍保留**连续两次采样**确认：单次读取异常/瞬时抖动不得直接拒单，
        否则任务被拒后中枢不会立即重派，用户侧表现为「排队中 0%」卡住
        （2026-09-28 现场）。metrics 全缺失时缺省放行，不因缺字段误伤。

        Returns:
            True 表示本节点当前不可自主接单。
        """
        for attempt in range(1, _BUSY_SAMPLE_COUNT + 1):
            available_gb = get_available_memory_gb()
            load_score = get_load_score()
            usage = get_resource_usage()
            cpu_percent = usage.get("cpu_percent")

            busy_memory = available_gb is not None and available_gb <= _MIN_AVAILABLE_MEMORY_GB
            busy_load = load_score is not None and load_score >= _MAX_LOAD_SCORE
            # load_score 缺失（心跳尚未采集过）时用 CPU 百分比兜底，避免漏判
            busy_cpu_fallback = (
                load_score is None
                and cpu_percent is not None
                and cpu_percent >= self.config.agent_busy_cpu_threshold_percent
            )
            if not (busy_memory or busy_load or busy_cpu_fallback):
                return False
            if attempt < _BUSY_SAMPLE_COUNT:
                logger.info(
                    "task_dispatch_busy_sample",
                    extra={
                        "node_id": self.node_id,
                        "attempt": attempt,
                        "available_memory_gb": available_gb,
                        "load_score": load_score,
                        "cpu_percent": cpu_percent,
                    },
                )
                time.sleep(_BUSY_SAMPLE_INTERVAL_SECONDS)
        logger.warning(
            "task_dispatch_resource_busy_confirmed",
            extra={
                "node_id": self.node_id,
                "samples": _BUSY_SAMPLE_COUNT,
                "available_memory_gb": get_available_memory_gb(),
                "load_score": get_load_score(),
                "min_available_memory_gb": _MIN_AVAILABLE_MEMORY_GB,
                "max_load_score": _MAX_LOAD_SCORE,
            },
        )
        return True

    async def _run_task(self, dispatch: TaskDispatchPayload) -> None:
        """执行任务并在完成后从在跑字典中移除。

        ADR-055 P0-3：桥接层可能抛出未预期异常（如 ``ValueError``/
        ``TypeError``/``KeyError``）。此处在 finally 之外兜底捕获并**强制
        上报失败**，避免中枢永久等待一个不会到来的终态；``CancelledError``
        属正常取消（节点关闭/任务取消），不误报失败。
        """
        task_id = dispatch.task_id
        try:
            bridge = self.task_bridge
            if bridge is not None:
                await bridge.execute_task(dispatch)
        except asyncio.CancelledError:
            logger.info(
                "task_execution_cancelled",
                extra={"task_id": task_id, "node_id": self.node_id},
            )
            raise
        except BaseException as exc:  # noqa: BLE001 — 兜底必须捕获全部异常类型
            logger.error(
                "task_execution_crashed",
                extra={
                    "event": "task_execution_crashed",
                    "task_id": task_id,
                    "node_id": self.node_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                exc_info=True,
            )
            await self.report_failed(task_id, f"agent_internal_error: {type(exc).__name__}: {exc}")
        finally:
            self._execution_tasks.pop(task_id, None)

    async def _send_event(self, event: str, payload: dict[str, Any]) -> None:
        """通过 WebSocket 发送事件；断线时任务生命周期事件进入缓冲待补报。

        缓冲策略（ADR-020）：``accepted/completed/failed`` 属任务生命周期
        关键事件，断线期间入队（上限 ``_PENDING_EVENTS_MAX``），重连后按序
        补报；``progress`` 等过程事件已过时直接丢弃，重连后下一次上报自然
        恢复。中枢侧任务归属校验保证过期补报不会污染已改派的任务。

        Args:
            event: 事件名。
            payload: 事件负载。
        """
        message = self._build_ws_message(event, payload)
        if _is_websocket_closed(self.websocket):
            self._buffer_event(event, payload)
            return

        try:
            await self.websocket.send(message)
        except Exception as exc:
            # 发送失败（连接正在断开）：生命周期事件同样进入缓冲，避免终态丢失
            logger.warning(
                "event_send_failed",
                extra={"event": event, "node_id": self.node_id, "error": str(exc)},
            )
            self._buffer_event(event, payload)
            return
        logger.debug("event_sent", extra={"event": event, "node_id": self.node_id})

    def _build_ws_message(self, event: str, payload: dict[str, Any]) -> str:
        """构造统一格式的 WebSocket 消息 JSON 字符串。

        Args:
            event: 事件名。
            payload: 事件负载。

        Returns:
            序列化后的 JSON 消息字符串。
        """
        return json.dumps(
            {
                "event": event,
                "payload": payload,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_id": None,
            }
        )

    def _buffer_event(self, event: str, payload: dict[str, Any]) -> None:
        """断线时缓冲任务生命周期事件；过程类事件仅记录丢弃日志。

        Args:
            event: 事件名。
            payload: 事件负载。
        """
        if event not in _BUFFERED_EVENTS:
            logger.warning(
                "event_dropped_offline",
                extra={"event": event, "node_id": self.node_id},
            )
            return
        self._pending_events.append((event, payload))
        logger.warning(
            "event_buffered_offline",
            extra={
                "event": event,
                "node_id": self.node_id,
                "task_id": payload.get("task_id"),
                "pending_count": len(self._pending_events),
            },
        )

    async def _flush_pending_events(self) -> None:
        """重连成功后按序补报断线期间缓冲的任务生命周期事件（ADR-020）。

        过期补报由中枢归属校验（task_event_handler._get_verified_task）安全
        拒绝：任务已改派他人或已终态时上报被丢弃，不影响新归属节点。单条
        发送失败即停止本次冲刷（连接可能再次断开，剩余事件留待下轮补报）。
        """
        if not self._pending_events:
            return
        flushed = 0
        while self._pending_events:
            event, payload = self._pending_events[0]
            if _is_websocket_closed(self.websocket):
                break
            try:
                await self.websocket.send(self._build_ws_message(event, payload))
            except Exception as exc:
                logger.warning(
                    "event_flush_send_failed",
                    extra={"event": event, "node_id": self.node_id, "error": str(exc)},
                )
                break
            self._pending_events.popleft()
            flushed += 1
            logger.info(
                "buffered_event_flushed",
                extra={"event": event, "task_id": payload.get("task_id"), "node_id": self.node_id},
            )
        logger.info(
            "pending_events_flushed",
            extra={
                "node_id": self.node_id,
                "flushed": flushed,
                "remaining": len(self._pending_events),
            },
        )

    async def _send_task_accepted(self, task_id: str) -> None:
        """回复任务已接受。"""
        await self._send_event(_TASK_ACCEPTED_EVENT, {"task_id": task_id})

    async def _send_task_rejected(
        self, task_id: str, reason: str, message: str | None = None
    ) -> None:
        """回复任务被拒绝，可携带人类可读说明。"""
        payload: dict[str, Any] = {"task_id": task_id, "reason": reason}
        if message is not None:
            payload["message"] = message
        await self._send_event(_TASK_REJECTED_EVENT, payload)

    async def _send_task_progress(
        self,
        task_id: str,
        progress: int,
        todos: list[dict[str, Any]] | None = None,
        *,
        stage: str | None = None,
        message: str | None = None,
        tool: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """上报任务进度（ADR-055：携带执行阶段/当前动作/工具/可观测明细）。"""
        payload: dict[str, Any] = {"task_id": task_id, "progress": progress}
        if stage is not None:
            payload["stage"] = stage
        if message is not None:
            payload["message"] = message[:_PROGRESS_MESSAGE_MAX_CHARS]
        if tool is not None:
            payload["tool"] = tool
        if detail:
            payload["detail"] = detail
        if todos is not None:
            payload["todos"] = todos
        await self._send_event(_TASK_PROGRESS_EVENT, payload)

    async def _send_task_completed(
        self,
        task_id: str,
        session_id: str,
        plan_content: str | None = None,
        change_summary: str | None = None,
        project_context: str | None = None,
        verify_report: dict[str, Any] | None = None,
    ) -> None:
        """上报任务已完成（payload 与中枢 TaskCompletedPayload 对齐）。

        plan_content / change_summary / project_context / verify_report 非 None 时
        加入 payload（方案全文 / 本轮修改说明 / 项目上下文记忆 / 验收报告），
        None 时不携带该键，保持与既有完成事件格式兼容。
        """
        payload: dict[str, Any] = {"task_id": task_id, "session_id": session_id}
        if plan_content is not None:
            payload["plan_content"] = plan_content
        if change_summary is not None:
            payload["change_summary"] = change_summary
        if project_context is not None:
            payload["project_context"] = project_context
        if verify_report is not None:
            payload["verify_report"] = verify_report
        await self._send_event(_TASK_COMPLETED_EVENT, payload)

    async def _send_task_failed(self, task_id: str, reason: str) -> None:
        """上报任务失败（payload 与中枢 TaskFailedPayload 对齐）。"""
        await self._send_event(_TASK_FAILED_EVENT, {"task_id": task_id, "reason": reason})

    async def report_file_tree(
        self, task_id: str, files: list[dict[str, Any]], truncated: bool
    ) -> None:
        """上报任务产物文件清单（ADR-028；执行中周期与完成终态共用）。"""
        await self._send_event(
            _FILE_TREE_EVENT,
            {"task_id": task_id, "files": files, "truncated": truncated},
        )

    async def send_file_chunk(
        self, request_id: str, seq: int, total: int, data_b64: str, done: bool
    ) -> None:
        """回传产物拉取分块（ADR-028；过程类事件，断线时丢弃由中枢超时重试）。"""
        await self._send_event(
            _FILE_CHUNK_EVENT,
            {"request_id": request_id, "seq": seq, "total": total, "data": data_b64, "done": done},
        )

    async def report_file_error(self, request_id: str, reason: str) -> None:
        """上报产物拉取失败（ADR-028）。"""
        await self._send_event(_FILE_ERROR_EVENT, {"request_id": request_id, "reason": reason})
