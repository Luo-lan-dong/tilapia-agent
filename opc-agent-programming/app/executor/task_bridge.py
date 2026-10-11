"""任务桥接核心：中枢任务派发 → tilacode 会话执行 → 结果回调。

编排工作目录准备、Session 创建/复用、prompt 下发与 SSE 消费（见
``sse_consumer``），并通过 ``BridgeCallbacks`` 把进度/完成/失败上报回
中枢客户端；同时提供权限兜底泵与 tilacode 崩溃联动处理。

注：本文件超过 300 行上限（见 docs/standards/coding-standards.md 4.3）。
任务执行编排（准备 → 执行 → 结果分发）为单一职责内的强内聚流程，拆分
会引入不必要的间接层，故按豁免场景保留并在此说明。
"""

import asyncio
import base64
import io
import logging
import shutil
import time
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import AgentConfig
from app.executor import permission_policy, verify_runner, verify_tamper
from app.executor.agent_trace import AgentTraceCollector
from app.executor.event_translator import EventTranslator, ProgressUpdate, is_permission_event
from app.executor.file_scanner import scan_file_tree
from app.executor.session_store import SessionStore, SessionStoreError, TaskMappingStore
from app.executor.sse_consumer import ConsumeResult, SSEConsumer
from app.executor.tilacode_client import (
    _DEFAULT_MODEL_ID,
    _DEFAULT_MODEL_PROVIDER,
    TilacodeAPIError,
    TilacodeClient,
)
from app.executor.workspace import (
    WorkspaceManager,
    WorkspacePathError,
    provider_namespace,
    spec_docs_relative_dir,
)
from app.models.schemas import TaskDispatchPayload

#: 执行中文件清单周期上报间隔（秒，ADR-028）
_FILE_TREE_REPORT_INTERVAL_SECONDS = 30
#: 产物分块大小（字节，ADR-028）
_FILE_CHUNK_SIZE = 512 * 1024
#: 打包产物大小上限（字节，与 hub 侧一致）
_MAX_PACK_BYTES = 500 * 1024 * 1024
#: 附件压缩包解压条目数上限（防 zip 炸弹，ADR-039 P2）
_ARCHIVE_MAX_ENTRIES = 5000
#: 附件压缩包内单文件解压后大小上限（字节，ADR-039 P2）
_ARCHIVE_MAX_MEMBER_BYTES = 200 * 1024 * 1024

#: 无进度看门狗轮询间隔（秒，ADR-055 P0-2）
_WATCHDOG_POLL_INTERVAL_SECONDS = 10.0
#: 启动停滞判定阈值（秒，ADR-055 补丁）：prompt admitted 后仍无 step.started
#: 即判失败并给出可操作原因（tilacode 的模型解析/前置失败不产生事件，
#: 否则要等满无进度看门狗 1800s 才暴露，排查成本极高）
_STARTUP_STALL_TIMEOUT_SECONDS = 60.0
#: 周期任务最小真实等待（秒）：即使 ``asyncio.sleep`` 被替换为让出式实现
#: （测试或异常环境），周期循环也按真实耗时自我节流，避免霸占事件循环、
#: 饿死正在干活的 SSE 消费任务。
_PERIODIC_MIN_SLEEP_SECONDS = 0.2
#: handoff 包条目数 / 单文件大小上限（ADR-055 P2-12，复刻 ADR-039 附件策略）
_HANDOFF_MAX_ENTRIES = 5000
_HANDOFF_MAX_MEMBER_BYTES = 200 * 1024 * 1024

#: 内置默认免费模型描述（与 tilacode_client 内置默认一致，ADR-029）
_FREE_MODEL_DESC = f"{_DEFAULT_MODEL_PROVIDER}/{_DEFAULT_MODEL_ID}"

#: 节点续聊轮独立超时（秒，ADR-064 风险③）：续聊轮完全绕开任务状态机、
#: 不触发任务级无进度看门狗，由本轮独立轻量超时兜底；超时回 error("timeout")。
#: 另有续聊自己的无进展看门狗（_NODE_CHAT_WATCHDOG_SECONDS）防会话挂起干等。
_NODE_CHAT_TIMEOUT_SECONDS: float = 300.0
#: 续聊无进展看门狗窗口（秒，2026-10-06 用户实测：interrupt 后 tilacode 会话
#: runner 挂起——send_prompt 200 但整轮零事件，干等 300s 超时）：窗口内零轨迹行
#: 且零进度明细判定为会话无响应，触发清场重发/快速失败
_NODE_CHAT_WATCHDOG_SECONDS: float = 60.0


async def _noop_node_chat_reply(
    *,
    reply_to: str,
    task_id: str,
    root_task_id: str,
    text: str,
    tokens: dict[str, Any] | None = None,
) -> None:
    """节点续聊回复上行回调的默认空实现（未注入时的安全兜底，ADR-064 M2）。"""


async def _noop_node_chat_error(
    *, reply_to: str, task_id: str | None, reason: str
) -> None:
    """节点续聊错误上行回调的默认空实现（未注入时的安全兜底，ADR-064 M2）。"""


def _sanitize_fetch_path(path: str | None) -> str:
    """清洗产物拉取路径：拒绝空串、绝对路径与 ``..`` 越界片段。"""
    if not path:
        raise FileNotFoundError("路径为空")
    normalized = path.strip().lstrip("/")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise FileNotFoundError(f"非法路径: {path}")
    return "/".join(parts)


def _is_safe_archive_member(name: str) -> bool:
    """判断压缩包成员路径是否安全（ADR-055 P2-12）。

    拒绝：空名、绝对路径（``/`` 或 ``C:\\``）、反斜杠分隔（Windows 语义穿透）、
    含 ``..`` 片段（``/`` 或 ``\\`` 分隔均查）、以及回写为绝对/越界路径的成员。
    """
    if not name or name.startswith("/") or name.startswith("\\"):
        return False
    if "\\" in name:
        return False
    if len(name) > 1 and name[1] == ":":
        return False
    return not any(part == ".." for part in name.split("/"))


def _last_assistant_text_from_messages(messages: list[dict[str, Any]]) -> str | None:
    """从会话上下文消息数组提取最后一条 assistant 文本（ADR-055 P0-5）。

    兼容两种 parts 结构：``session.next`` 消息的 ``content``（含
    ``type: "text"`` 项）与旧格式的 ``parts``。防御式解析，取不到返回 None。
    """
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for key in ("content", "parts"):
            items = message.get(key)
            if not isinstance(items, list):
                continue
            texts = [
                item.get("text")
                for item in items
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            text = "\n".join(t for t in texts if isinstance(t, str) and t).strip()
            if text:
                return text
    return None


def _last_assistant_text_from_events(events: list[dict[str, Any]]) -> str | None:
    """从 durable 事件分页提取最后一条 assistant 文本（回退路径）。

    真实 ``/history`` 返回 ``SessionEvent.Durable`` 数组；完成回合的文本以
    ``session.next.text.ended`` 事件承载（``data.text``），故按事件类型倒序
    查找。
    """
    for event in reversed(events):
        if not isinstance(event, dict) or event.get("type") != "session.next.text.ended":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            data = event.get("properties")
        if not isinstance(data, dict):
            continue
        text = data.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


class TaskBridgeError(Exception):
    """任务桥接层异常。"""


async def _noop_verify_blocked(task_id: str, project_key: str, report: dict[str, Any]) -> None:
    """验收阻断回调的默认空实现（未注入时的安全兜底）。"""


async def _noop_agent_trace(
    task_id: str,
    *,
    node_id: str | None = None,
    session_id: str | None = None,
    rows: list[dict[str, Any]] | None = None,
) -> None:
    """节点轨迹上报回调的默认空实现（未注入时的安全兜底，ADR-063 M1）。"""


@dataclass
class BridgeCallbacks:
    """任务结果回调集合。

    on_progress(task_id, progress, todos, *, stage, message, tool, detail)——
    进度回调（ADR-055 起携带执行阶段/当前动作/工具/可观测明细）；
    on_task_started(task_id, executor_model_actual)、
    on_completed(task_id, session_id, plan_content, change_summary, project_context)、
    on_failed(task_id, reason)、on_file_tree(task_id, files, truncated)、
    on_file_chunk / on_file_error（ADR-028 产物回传），由中枢客户端实现后注入。

    plan_content 仅在 plan 模式任务完成时携带方案全文；change_summary 仅在
    build 模式任务完成时携带本轮修改说明；project_context 为 AI 生成的项目
    上下文记忆（ADR-034，工作目录 project_context.md 内容），三者非对应
    场景时均为 None。
    """

    on_progress: Callable[..., Awaitable[None]]
    on_task_started: Callable[[str, str], Awaitable[None]]
    on_completed: Callable[..., Awaitable[None]]
    on_failed: Callable[[str, str], Awaitable[None]]
    on_file_tree: Callable[[str, list[dict[str, Any]], bool], Awaitable[None]]
    on_file_chunk: Callable[[str, int, int, str, bool], Awaitable[None]]
    on_file_error: Callable[[str, str], Awaitable[None]]
    #: 验收闸阻断回调（ADR-061 M4）：(task_id, project_key, 报告) ——
    #: 红/无法验证且自修用尽时调用，中枢据此进入「等用户决定」态（verify_blocked）
    on_verify_blocked: Callable[[str, str, dict[str, Any]], Awaitable[None]] = (
        _noop_verify_blocked
    )
    #: 节点 Agent 轨迹上报回调（ADR-063 M1）：(task_id, node_id=, session_id=, rows=)，
    #: 由 HubClient 装配时通过 ``bind_agent_trace`` 注入（HTTP 旁路）；未注入时为空实现
    on_agent_trace: Callable[..., Awaitable[None]] = _noop_agent_trace
    #: 节点续聊回复上行回调（ADR-064 M2）：由 HubClient 装配时通过
    #: ``bind_node_chat_channels`` 注入（``agent.node_chat_reply``）；未注入时为空实现
    on_node_chat_reply: Callable[..., Awaitable[None]] = _noop_node_chat_reply
    #: 节点续聊错误上行回调（ADR-064 M2）：由 HubClient 装配时通过
    #: ``bind_node_chat_channels`` 注入（``agent.node_chat_error``）；未注入时为空实现
    on_node_chat_error: Callable[..., Awaitable[None]] = _noop_node_chat_error


#: 验收未通过时的自修提示（ADR-061 M4④：带失败命令与输出尾部，最多 5 轮）
_VERIFY_REPAIR_PROMPT_HEADER = (
    "自动验收未通过，请修复后重新自检（这是自动流程，不需要询问用户）：\n"
)
_VERIFY_REPAIR_PROMPT_TAILER = (
    "\n\n要求：\n"
    "1. 逐条修复上述失败项（必要时先读代码定位原因）；\n"
    "2. 修完后**自己重新执行**这些命令，确认全部通过；\n"
    "3. 不要修改验收判据本身来「绕过」失败；\n"
    "4. 若某项确实无法通过，请在回复中明确说明原因与建议，不要伪造通过。"
)
#: 完成闸自修轮次上限（用户已定 ≤5）
VERIFY_MAX_REPAIR_ROUNDS = 5


# build 模式完成后的总结 prompt：产出本轮修改说明（见 ADR-012 change_summary 增补决策）
_SUMMARY_PROMPT = (
    "请用中文输出本轮修改说明，供项目负责人审阅：\n"
    "1. 列出本轮修改/新增的文件及其作用\n"
    "2. 概述核心改动点与实现方式\n"
    "3. 说明任何未完成事项或已知风险\n"
    "只输出说明文本，不要执行任何代码修改。"
)


async def _noop_progress(_update: ProgressUpdate) -> None:
    """总结轮的进度空回调：避免总结阶段进度值回退抖动。"""


# 权限泵重连间隔（秒）：实例级流断开后稍候重订
_PERMISSION_PUMP_RECONNECT_SECONDS: float = 5.0

# 实例级 SSE 事件类型
_EVENT_TODO_UPDATED: str = "todo.updated"


class TaskBridge:
    """把中枢任务派发转换为 tilacode 会话执行的桥接器。"""

    def __init__(
        self,
        config: AgentConfig,
        client: TilacodeClient,
        store: SessionStore,
        workspace: WorkspaceManager,
        callbacks: BridgeCallbacks,
        mapping_store: TaskMappingStore | None = None,
    ) -> None:
        """初始化桥接器：config 提供静默判定时长等配置，client 为 tilacode
        HTTP 客户端，store 为会话登记簿，workspace 为工作目录管理器，
        callbacks 为进度/完成/失败回调集合，mapping_store 为任务派发映射
        持久化登记簿（None 时仅内存映射，重启后 file_fetch 反查退化）。
        """
        self._config = config
        self._client = client
        self._store = store
        self._workspace = workspace
        self._callbacks = callbacks
        self._mapping_store = mapping_store
        self._logger = logging.getLogger(__name__)
        self._current_tasks: dict[str, TaskDispatchPayload] = {}
        self._consumers: dict[str, SSEConsumer] = {}
        # 任务 ID → (项目键, 显式 output_dir) 映射，供产物拉取按执行时同一
        # 参数反查工作目录（ADR-028；ADR-036 起任务带显式 output_dir，
        # 只回放 project_key 会解析到默认空目录导致 fetch 恒报文件不存在）。
        # mapping_store 提供持久化：Agent 重启后仍可反查（2026-09-23）。
        self._task_project_keys: dict[str, tuple[str, str | None]] = {}
        # Session ID → 工作目录映射，供权限回复、中断等按 session 反查目录
        self._session_directories: dict[str, Path] = {}
        # task_id → 上次进度上报时间（单调时钟，供静默期心跳去重，ADR-055 P2-13）
        self._last_progress_report_at: dict[str, float] = {}
        #: 最近一次上报的进度值（ADR-061 M5a-steer：定向发言的送达确认要带上它，
        #: 避免用 0 把中枢进度条打回去）
        self._last_progress_value: dict[str, int] = {}
        #: Session ID → 实际生效的权限策略（ADR-061 M2b）：权限询问判定与审计用；
        #: 任务收尾时清理，缺失时回读工作区配置（配置文件即策略缓存，Q2）
        self._session_policies: dict[str, dict[str, Any]] = {}
        # task_id → 当前 SSE 消费任务（供轮次切换时结束上一轮遗留消费，ADR-055）
        self._consume_tasks: dict[str, asyncio.Task[ConsumeResult]] = {}
        # task_id → 轨迹采集器（ADR-063 M1/M2）：seq 统一分配、批量上报，
        # 任务收尾时 close() 排水并清理，避免字典随任务数无限增长
        self._trace_collectors: dict[str, AgentTraceCollector] = {}
        #: project_key → 判据防篡改 mtime 基线（ADR-061 验收 #12 修订 2026-10-03）：
        #: 在 _prepare_session 末尾（prompt 下发前）拍摄，gate 复查时弹出使用；
        #: 模型在「工作阶段」自建/改写探针同样落在快照之后，可被检出
        self._tamper_baselines: dict[str, dict[str, float]] = {}
        #: 进行中续聊轮的会话集合（ADR-064 M2）：同一会话并发 node_chat 保护，
        #: 与 _current_tasks 完全独立（红线：续聊不触碰任何任务态字典）
        self._node_chat_sessions: set[str] = set()

    def bind_agent_trace(self, reporter: Callable[..., Awaitable[None]]) -> None:
        """注入节点轨迹上报回调（ADR-063 M1，由 HubClient 装配时调用）。

        轨迹上报是 HTTP 旁路（``HubClient.report_agent_trace``），由中枢客户端
        持有；桥接层只在「派发开始 / 会话结束」两个时点回调。未注入时保持默认
        空实现，其他装配方式（含测试）不受影响。
        """
        self._callbacks.on_agent_trace = reporter

    def bind_node_chat_channels(
        self,
        reply: Callable[..., Awaitable[None]],
        error: Callable[..., Awaitable[None]],
    ) -> None:
        """注入节点续聊回复/错误上行回调（ADR-064 M2，由 HubClient 装配时调用）。

        回复走 ``agent.node_chat_reply``、错误走 ``agent.node_chat_error``，均由
        中枢客户端经 WebSocket 上行；未注入时保持默认空实现（测试装配不受影响）。
        """
        self._callbacks.on_node_chat_reply = reply
        self._callbacks.on_node_chat_error = error

    async def execute_task(self, payload: TaskDispatchPayload) -> None:
        """执行单个任务：准备环境 → 建 Session → 发 prompt → 消费事件 → 回调。

        Args:
            payload: 中枢下发的任务派发消息。
        """
        task_id = payload.task_id
        project_key = payload.base_task_id or task_id
        # 记录 task_id → (项目键, output_dir) 映射：产物拉取（hub.file_fetch）
        # 按任务 ID 以与执行完全相同的参数反查工作目录；同步持久化，
        # Agent 重启后反查不退化（2026-09-23）
        self._task_project_keys[task_id] = (project_key, payload.output_dir)
        if self._mapping_store is not None:
            await self._mapping_store.put(task_id, project_key, payload.output_dir)
        self._current_tasks[task_id] = payload
        # 轨迹采集器（ADR-063 M2）：本任务的 seq/批量/脱敏/背压都在此收敛，
        # 上报回调取当前注入值（HubClient 装配时已通过 bind_agent_trace 绑定）
        collector = AgentTraceCollector(task_id, reporter=self._callbacks.on_agent_trace)
        self._trace_collectors[task_id] = collector
        # 关键节点日志（见 agent-node-standards 第 6 节）：接收在 hub_client 记录，此处记录开始执行
        self._logger.info(
            "task_execution_started",
            extra={"task_id": task_id, "project_key": project_key, "mode": payload.mode},
        )
        file_tree_task: asyncio.Task[None] | None = None
        event_pump_task: asyncio.Task[None] | None = None
        watchdog_task: asyncio.Task[None] | None = None
        heartbeat_task: asyncio.Task[None] | None = None
        # ADR-055 P0-3：_prepare_session 可能在赋值前失败，finally 必须容忍
        session_id: str | None = None
        try:
            session_id, ws_dir, doc_paths = await self._prepare_session(payload)
            # 规则文档落盘（ADR-048）：.tilacode/rules/ + AGENTS.md 加载索引
            # （hub_client 下载校验后的字节；下载失败已降级为跳过）
            if payload.rule_doc_blobs:
                # ADR-055 P1-7：链上后续任务复用同一工作目录，规则文档/AGENTS.md
                # 内容与上轮一致时**不重写**——重写会改变 tilacode 的
                # instructions 基线，使会话上下文 epoch 产生无谓的增量注入，
                # 加速长任务链的上下文膨胀。
                if await asyncio.to_thread(
                    self._workspace.rule_docs_materialized, ws_dir, payload.rule_doc_blobs
                ):
                    self._logger.info(
                        "rule_docs_reuse_existing",
                        extra={"task_id": task_id, "doc_count": len(payload.rule_doc_blobs)},
                    )
                else:
                    await asyncio.to_thread(
                        self._workspace.materialize_rule_docs, ws_dir, payload.rule_doc_blobs
                    )
            # 任务附件落盘（ADR-039）：.hub-attachments/ 供 AI 读取与 prompt 引用
            attachment_paths = await asyncio.to_thread(self._persist_attachments, ws_dir, payload)
            # 任务级实例事件泵：todo.updated/权限事件的目录为任务工作目录，
            # 必须以任务目录订阅才能收到（全局根目录订阅会被目录过滤丢弃）
            event_pump_task = asyncio.create_task(
                self._task_event_pump(task_id, ws_dir), name=f"instance-events-{task_id}"
            )
            # 执行中周期上报产物文件清单（ADR-028），任务结束时终止
            file_tree_task = asyncio.create_task(
                self._file_tree_reporter(task_id, ws_dir), name=f"file-tree-{task_id}"
            )
            # ADR-055 P2-13：静默期进度心跳，长 step 期间前端不再「假死」
            heartbeat_task = asyncio.create_task(
                self._progress_heartbeat(task_id), name=f"heartbeat-{task_id}"
            )
            prompt = self._build_prompt(payload, doc_paths, ws_dir, attachment_paths)
            # ADR-039：执行模型支持视觉时图片随 prompt 以 FileParts 发送
            image_parts = self._build_image_parts(payload)
            # ADR-055 P0-2：完成判定启动后即挂看门狗，覆盖「prompt 未返回」的死锁场景
            watchdog_task = asyncio.create_task(
                self._task_watchdog(session_id, task_id),
                name=f"watchdog-{task_id}",
            )
            result = await self._run_session(
                session_id, task_id, prompt, directory=str(ws_dir), image_parts=image_parts
            )
            await self._dispatch_result(payload, session_id, ws_dir, result)
        except (
            TilacodeAPIError,
            TaskBridgeError,
            SessionStoreError,
            WorkspacePathError,
            OSError,
        ) as exc:
            reason = f"task_bridge_error: {exc}"
            self._logger.error("task_bridge_error", extra={"task_id": task_id, "error": str(exc)})
            await self._dispatch_failure(task_id, reason, project_key)
        finally:
            for background in (file_tree_task, event_pump_task, watchdog_task, heartbeat_task):
                if background is not None:
                    background.cancel()
                    await asyncio.gather(background, return_exceptions=True)
            self._current_tasks.pop(task_id, None)
            self._consumers.pop(task_id, None)
            self._last_progress_report_at.pop(task_id, None)
            self._last_progress_value.pop(task_id, None)
            # 轨迹采集器随任务结束排水并清理（ADR-063 M1/M2）
            self._trace_collectors.pop(task_id, None)
            await collector.close()
            linger = self._consume_tasks.pop(task_id, None)
            if linger is not None and not linger.done():
                linger.cancel()
                await asyncio.gather(linger, return_exceptions=True)
            if session_id is not None:
                self._session_directories.pop(session_id, None)
                self._session_policies.pop(session_id, None)
            # 防篡改基线兜底清理：正常路径由完成闸弹出，异常终止时防字典累积
            self._tamper_baselines.pop(project_key, None)

    async def interrupt(self, task_id: str) -> None:
        """中断指定执行中的任务。

        简化语义：中断后按 ``failed(reason="interrupted")`` 上报中枢
        （中枢 MVP 无 interrupted 终态，failed 便于触发重试/人工介入）。
        """
        consumer = self._consumers.get(task_id)
        current = self._current_tasks.get(task_id)
        if consumer is None or current is None:
            self._logger.warning("task_bridge_interrupt_noop", extra={"task_id": task_id})
            return
        consumer.mark_interrupted()
        # Session 以任务链根任务（project_key）归属（见 ADR-012 任务迭代链决策）
        project_key = current.base_task_id or current.task_id
        session_id = f"ses_task_{project_key}"
        ws_dir = self._session_directories.get(session_id)
        try:
            await self._client.interrupt(session_id, directory=str(ws_dir) if ws_dir else None)
        except TilacodeAPIError as exc:
            # 中断 API 失败不致命：消费循环仍会在静默复核后按 interrupted 结束
            self._logger.warning(
                "task_bridge_interrupt_api_failed",
                extra={"task_id": task_id, "error": str(exc)},
            )

    async def steer(self, task_id: str, text: str) -> bool:
        """向执行中任务的活动会话定向发言（ADR-061 M5a-steer）。

        与 ``interrupt`` 的区别：**不中断任务**，把这段话以 steer 方式追加进会话，
        影响后续行为；任务自身继续跑到终态。

        会话归属与 ``interrupt`` 一致：``ses_task_{base_task_id or task_id}``（任务链根任务）。

        Args:
            task_id: 任务 ID。
            text: 要送进会话的文本（调用方已保证非空）。

        Returns:
            True 表示已送达会话（并已上报送达确认）；False 表示任务不在本节点执行。
        """
        current = self._current_tasks.get(task_id)
        if current is None:
            self._logger.info("task_bridge_steer_noop", extra={"task_id": task_id})
            return False
        project_key = current.base_task_id or current.task_id
        session_id = f"ses_task_{project_key}"
        ws_dir = self._session_directories.get(session_id)
        await self._client.send_prompt(session_id, text, directory=str(ws_dir) if ws_dir else None)
        # 送达确认：让中枢立刻看到"指令已到达"，而不是只能靠猜。
        # 只透传已知进度（不知道时不发，避免把进度条打回 0）；stage/todos 传 None 由中枢保持原值。
        known_progress = self._last_progress_value.get(task_id)
        if known_progress is not None:
            summary = " ".join(text.split())[:60]
            await self._callbacks.on_progress(
                task_id,
                known_progress,
                None,
                stage=None,
                message=f"已收到中枢指令：{summary}",
                tool=None,
                detail=None,
            )
        self._logger.info(
            "task_bridge_steer_sent",
            extra={"task_id": task_id, "session_id": session_id, "text_length": len(text)},
        )
        return True

    async def node_chat(self, payload: dict[str, Any]) -> None:
        """节点续聊对话（ADR-064 M2）：对既有会话续发消息并回传本轮回复。

        **完全绕开任务状态机**（红线）：不进 ``_current_tasks``、不改任务状态、
        不触发任务级无进度看门狗/完成闸/并发槽；任务态字典全程只读。prompt 以
        ``delivery="queue"`` 下发——会话空闲即开新轮，恰好活跃则入队（竞态安全）。
        本轮自带无进展看门狗（60s 零事件 → interrupt 重发一次 → 仍无 →
        ``session_unresponsive`` 快速失败）。

        流程：busy 并发保护 → 会话可用性校验（失败回 ``session_missing``）→
        临时轨迹采集器（归属 ``trace_task_id``，seq 自 ``last_seq+1`` 接续）→
        消费本轮事件至结束 → 提取 assistant 全文上行回复。整轮由独立超时
        （300s）兜底，超时回 ``timeout`` 并尽力中断会话。

        Args:
            payload: 中枢 ``hub.node_chat`` 负载（契约见 ADR-064 事件表）。
        """
        task_id = payload.get("task_id")
        root_task_id = payload.get("root_task_id")
        text = payload.get("text")
        reply_to = payload.get("reply_to")
        trace_raw = payload.get("trace_task_id")
        trace_task_id = trace_raw if isinstance(trace_raw, str) and trace_raw else task_id
        last_seq_raw = payload.get("last_seq")
        last_seq = last_seq_raw if isinstance(last_seq_raw, int) and last_seq_raw >= 0 else 0
        if not (
            isinstance(task_id, str)
            and task_id
            and isinstance(root_task_id, str)
            and root_task_id
            and isinstance(text, str)
            and text.strip()
            and isinstance(reply_to, str)
            and reply_to
        ):
            self._logger.warning(
                "node_chat_invalid_payload", extra={"trace_task_id": trace_task_id}
            )
            return
        session_id = f"ses_task_{root_task_id}"
        if session_id in self._node_chat_sessions:
            self._logger.warning(
                "node_chat_busy",
                extra={"task_id": task_id, "session_id": session_id, "reply_to": reply_to},
            )
            await self._send_node_chat_error(reply_to, task_id, "busy")
            return
        self._node_chat_sessions.add(session_id)
        self._logger.info(
            "node_chat_started",
            extra={
                "task_id": task_id,
                "session_id": session_id,
                "trace_task_id": trace_task_id,
                "last_seq": last_seq,
                "text_length": len(text),
            },
        )
        try:
            ws_dir = await self._resolve_node_chat_session(session_id, root_task_id)
            if ws_dir is None:
                await self._send_node_chat_error(reply_to, task_id, "session_missing")
                return
            try:
                detail = await asyncio.wait_for(
                    self._run_node_chat_round(
                        session_id, ws_dir, task_id, trace_task_id, last_seq, text
                    ),
                    timeout=_NODE_CHAT_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                self._logger.warning(
                    "node_chat_timeout",
                    extra={
                        "task_id": task_id,
                        "session_id": session_id,
                        "timeout_seconds": _NODE_CHAT_TIMEOUT_SECONDS,
                    },
                )
                await self._interrupt_node_chat_session(session_id, ws_dir)
                await self._send_node_chat_error(reply_to, task_id, "timeout")
                return
            # 优先取本轮 SSE 轨迹捕获的 text 全文（所见即所得）；context
            # 端点解析为回退（2026-10-05 E2E 实测其对续聊轮取不到文本）。
            reply_text = (detail or {}).get("text")
            if not isinstance(reply_text, str) or not reply_text.strip():
                reply_text = await self._extract_last_assistant_text(
                    session_id, task_id, directory=str(ws_dir)
                )
            if reply_text is None:
                await self._send_node_chat_error(reply_to, task_id, "reply_extract_failed")
                return
            tokens = detail.get("tokens") if isinstance(detail, dict) else None
            await self._safe_callback(
                self._callbacks.on_node_chat_reply(
                    reply_to=reply_to,
                    task_id=task_id,
                    root_task_id=root_task_id,
                    text=reply_text,
                    tokens=tokens if isinstance(tokens, dict) else None,
                ),
                task_id,
            )
            self._logger.info(
                "node_chat_replied",
                extra={
                    "task_id": task_id,
                    "session_id": session_id,
                    "reply_to": reply_to,
                    "text_length": len(reply_text),
                    "has_tokens": tokens is not None,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 续聊轮兜底：任何异常都回错误事件
            self._logger.error(
                "node_chat_failed",
                extra={"task_id": task_id, "session_id": session_id, "error": str(exc)},
                exc_info=True,
            )
            await self._send_node_chat_error(reply_to, task_id, f"node_chat_failed: {exc}")
        finally:
            self._node_chat_sessions.discard(session_id)

    async def _send_node_chat_error(self, reply_to: Any, task_id: Any, reason: str) -> None:
        """上行续聊错误事件（旁路包装：回调异常只记日志，不影响本协程收尾）。"""
        await self._safe_callback(
            self._callbacks.on_node_chat_error(
                reply_to=reply_to, task_id=task_id, reason=reason
            ),
            task_id if isinstance(task_id, str) else None,
        )

    async def _resolve_node_chat_session(
        self, session_id: str, root_task_id: str
    ) -> Path | None:
        """定位续聊会话的工作目录并校验会话可用（ADR-064 M2）。

        工作目录从会话登记簿按根任务反查（登记簿持久化，节点重启后仍可用）；
        随后以 ``get_context`` 探测会话存在（tilacode 会话持久化于 storage，
        按 id + 目录惰性恢复；不调 create_session，避免覆写既有 agent/model）。
        任一步失败返回 None，由调用方回 ``session_missing``。
        """
        try:
            record = await self._store.get(root_task_id)
        except SessionStoreError as exc:
            self._logger.warning(
                "node_chat_store_lookup_failed",
                extra={"root_task_id": root_task_id, "error": str(exc)},
            )
            return None
        if record is None or not record.workspace_dir:
            self._logger.info(
                "node_chat_session_unknown", extra={"session_id": session_id}
            )
            return None
        ws_dir = Path(record.workspace_dir)
        try:
            await self._client.get_context(session_id, directory=str(ws_dir))
        except TilacodeAPIError as exc:
            self._logger.warning(
                "node_chat_session_probe_failed",
                extra={"session_id": session_id, "error": str(exc)},
            )
            return None
        return ws_dir

    async def _run_node_chat_round(
        self,
        session_id: str,
        ws_dir: Path,
        task_id: str,
        trace_task_id: str,
        last_seq: int,
        text: str,
    ) -> dict[str, Any] | None:
        """执行一轮续聊：临时采集器 + prompt(queue) + 消费至轮次结束。

        消费复用既有 SSEConsumer（drain 排水 + 静默 + ``session/active`` 短轮询
        完成判定）；权限请求复用实例级事件泵兜底（会话目录临时登记供按 session
        反查，轮次结束恢复）。轨迹采集器为本轮私有：不进 ``_trace_collectors``
        （那是任务态字典），finally 排水销毁。

        Args:
            session_id: 会话 ID（``ses_task_{root_task_id}``）。
            ws_dir: 会话工作目录。
            task_id: 链上最后任务 ID（日志与权限泵标记用）。
            trace_task_id: 轨迹归属任务 ID（上报路径参数）。
            last_seq: 中枢下发的轨迹 seq 基线（采集器自 last_seq+1 起续）。
            text: 用户续聊文本。

        Returns:
            ``{"text": 本轮回复全文|None, "tokens": 本轮用量|None}``——text 来自
            SSE 轨迹捕获（text ended 全文），供上行回复用。

        Raises:
            TaskBridgeError: 消费结果为失败（session.error / step.failed 等），
                或看门狗判定会话无响应（``session_unresponsive``）。
        """
        collector = AgentTraceCollector(
            trace_task_id, reporter=self._callbacks.on_agent_trace, start_seq=last_seq
        )
        collector.configure_session(session_id)
        captured: dict[str, Any] = {}
        captured_text: dict[str, str] = {}
        # 看门狗活动计数：轨迹行数 + 进度明细数（零增长 = 会话无响应）
        activity = {"count": 0}

        async def _capture_detail(update: ProgressUpdate) -> None:
            """记录最后一条 step 明细（tokens 上行用）；续聊轮不做进度上报。"""
            activity["count"] += 1
            if update.detail:
                captured["detail"] = update.detail

        def _capture_rows(rows: list[dict[str, Any]]) -> None:
            """轨迹行旁路（同步，SSEConsumer 同步回调）：上报并捕获回复全文。

            SSE 流经翻译器产出的 ``text`` 行即节点本轮回复全文（ADR-063 M2：
            text ended 全文），与折叠块展示所见即所得——比 context 端点解析
            更可靠（2026-10-05 E2E 实测 context 解析取不到续聊轮文本）。
            行结构为 ``{"kind", "payload"}``（dict，非对象）。
            """
            activity["count"] += len(rows)
            collector.emit_rows(rows)
            for row in rows:
                if not isinstance(row, dict) or row.get("kind") != "text":
                    continue
                payload = row.get("payload")
                if (
                    isinstance(payload, dict)
                    and isinstance(payload.get("text"), str)
                    and payload["text"].strip()
                ):
                    captured_text["text"] = payload["text"]

        consumer = SSEConsumer(
            client=self._client,
            task_id=trace_task_id,
            session_id=session_id,
            directory=str(ws_dir),
            translator=EventTranslator(trace_task_id),
            idle_timeout_seconds=self._config.tilacode_step_idle_timeout_seconds,
            on_progress=_capture_detail,
            on_trace_rows=_capture_rows,
        )
        prior_dir = self._session_directories.get(session_id)
        self._session_directories[session_id] = ws_dir
        pump = asyncio.create_task(
            self._task_event_pump(task_id, ws_dir), name=f"node-chat-events-{task_id}"
        )
        consume_task = asyncio.create_task(
            consumer.run(), name=f"node-chat-consume-{trace_task_id}"
        )
        try:
            await self._client.send_prompt(
                session_id, text, directory=str(ws_dir), delivery="queue"
            )
            # 无进展看门狗（2026-10-06 用户实测：interrupt 后 runner 挂起，
            # send_prompt 200 但整轮零事件、SSE 反复断连，干等 300s 超时且
            # 用户得不到任何回复）：每 60s 检查轮内活动。无进展 → interrupt
            # 清场后重发一次 prompt；重发后仍无进展 → 快速失败
            # session_unresponsive。事件一旦流动即视为正常轮次，交给
            # SSEConsumer 既有完成判定，看门狗退出（总 300s 超时仍兜底）。
            for phase in range(2):
                baseline = activity["count"]
                done, _ = await asyncio.wait(
                    {consume_task}, timeout=_NODE_CHAT_WATCHDOG_SECONDS
                )
                if done or activity["count"] != baseline:
                    break
                if phase == 0:
                    self._logger.warning(
                        "node_chat_no_progress_resend",
                        extra={
                            "task_id": task_id,
                            "session_id": session_id,
                            "trace_task_id": trace_task_id,
                            "quiet_seconds": int(_NODE_CHAT_WATCHDOG_SECONDS),
                        },
                    )
                    await self._interrupt_node_chat_session(session_id, ws_dir)
                    await self._client.send_prompt(
                        session_id, text, directory=str(ws_dir), delivery="queue"
                    )
                else:
                    consume_task.cancel()
                    await asyncio.gather(consume_task, return_exceptions=True)
                    await self._interrupt_node_chat_session(session_id, ws_dir)
                    raise TaskBridgeError("session_unresponsive")
            result = await consume_task
        except BaseException:
            # prompt 下发失败/协程被取消/看门狗快速失败：终止消费任务后原样上抛
            consume_task.cancel()
            await asyncio.gather(consume_task, return_exceptions=True)
            raise
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            if prior_dir is not None:
                self._session_directories[session_id] = prior_dir
            else:
                self._session_directories.pop(session_id, None)
            try:
                await collector.close()
            except Exception as exc:  # noqa: BLE001 — 轨迹旁路，收尾失败只告警
                self._logger.warning(
                    "node_chat_trace_close_failed",
                    extra={"task_id": trace_task_id, "error": str(exc)},
                )
        if result.status != "completed":
            raise TaskBridgeError(f"node_chat_round_failed: {result.reason}")
        detail = captured.get("detail")
        return {
            "text": captured_text.get("text"),
            "tokens": detail.get("tokens") if isinstance(detail, dict) else None,
        }

    async def _interrupt_node_chat_session(self, session_id: str, ws_dir: Path) -> None:
        """续聊轮超时后尽力中断会话（失败只告警，不影响错误上报）。"""
        try:
            await self._client.interrupt(session_id, directory=str(ws_dir))
        except Exception as exc:  # noqa: BLE001 — 中断失败不致命
            self._logger.warning(
                "node_chat_interrupt_failed",
                extra={"session_id": session_id, "error": str(exc)},
            )

    async def handle_tilacode_crash(self, reason: str) -> None:
        """tilacode 进程异常退出回调：使所有在跑任务失败结束（ADR-032）。

        进程退出必然导致 SSE 断流，消费循环会在断流后看到此处设置的
        失败结果并终止；store 更新与 on_failed 由 execute_task 失败路径完成。
        """
        if not self._current_tasks:
            return
        self._logger.error(
            "task_bridge_tilacode_crash",
            extra={"task_ids": list(self._current_tasks), "reason": reason},
        )
        for task_id, consumer in list(self._consumers.items()):
            consumer.fail(f"tilacode_crashed: {reason}")

    async def shutdown(self) -> None:
        """优雅关闭：关闭 tilacode 客户端、尽力中断所有执行中任务（ADR-032）。"""
        await self._client.close()
        for task_id in list(self._current_tasks):
            try:
                await self.interrupt(task_id)
            except Exception as exc:
                self._logger.warning(
                    "task_bridge_shutdown_interrupt_failed",
                    extra={"task_id": task_id, "error": str(exc)},
                )

    async def _preflight_session_model(
        self,
        ws_dir: Path,
        session_model: dict[str, str],
        task_id: str,
    ) -> None:
        """派发前预检：确认任务级 provider/模型真的可解析（ADR-061 C1）。

        2026-10-01 事故：tilacode 的模型目录（catalog）装配曾是后台任务，实例刚
        重建时目录还是空的，任务首轮必然解析失败，但外部只看到「卡到
        ``task_startup_stalled`` 后一句 ``ModelUnavailableError``」。此处主动问
        一次 tilacode（``GET /api/provider``），在**派发前**把这类问题变成一条
        带清单摘要的明确失败。

        判定策略：
        - 端点异常/不可用 → 仅告警（fail-open：预检本身绝不让任务失败）；
        - 返回的 provider 清单为空 → 目录未就绪（ERROR + 失败，附排查指引）；
        - 清单非空但缺 provider 或缺模型 → ERROR + 失败，列出已发现的 provider。
        """
        if not self._config.tilacode_dispatch_preflight:
            return
        provider_id = str(session_model.get("providerID") or "")
        model_id = str(session_model.get("id") or "")
        if not provider_id or not model_id:
            return
        try:
            snapshot = await self._client.provider_snapshot(directory=str(ws_dir))
        except Exception as exc:
            self._logger.warning(
                "tilacode_preflight_unavailable",
                extra={"task_id": task_id, "directory": str(ws_dir), "error": str(exc)},
            )
            return
        models = snapshot.get(provider_id)
        if models is not None and model_id in models:
            self._logger.info(
                "tilacode_preflight_ok",
                extra={"task_id": task_id, "provider": provider_id, "model": model_id},
            )
            return
        if not snapshot:
            self._logger.error(
                "tilacode_preflight_catalog_empty",
                extra={
                    "task_id": task_id,
                    "directory": str(ws_dir),
                    "provider": provider_id,
                    "model": model_id,
                },
            )
            raise TaskBridgeError(
                f"tilacode 模型目录为空，无法解析执行模型 {provider_id}/{model_id}："
                "节点侧 tilacode 未能装配 provider 目录（产物版本过旧或实例未就绪）。"
                "请检查节点 tilacode 版本（debug v2 的 provider 数应大于 0）后重派。"
            )
        self._logger.error(
            "tilacode_preflight_model_missing",
            extra={
                "task_id": task_id,
                "directory": str(ws_dir),
                "provider": provider_id,
                "model": model_id,
                "providers": sorted(snapshot)[:20],
                "models": sorted(models or [])[:20],
            },
        )
        raise TaskBridgeError(
            f"tilacode 无法解析执行模型 {provider_id}/{model_id}："
            f"{'provider 存在但缺少该模型' if models is not None else 'provider 不存在'}。"
            f"已发现的 provider：{', '.join(sorted(snapshot)[:10]) or '无'}"
        )

    async def _prepare_session(self, payload: TaskDispatchPayload) -> tuple[str, Path, list[str]]:
        """准备工作目录与 tilacode Session 并登记。

        工作目录、Session 与登记簿均以 project_key（任务链根任务）为维度，
        链上后续任务复用根任务目录与 Session（见 ADR-012 任务迭代链决策）。

        Args:
            payload: 任务派发消息。

        Returns:
            (session_id, 工作目录绝对路径, 已写入规格文档的相对路径列表)。
        """
        project_key = payload.base_task_id or payload.task_id
        ws_dir = self._workspace.prepare(project_key, output_dir=payload.output_dir)
        # 现场迁移（ADR-034）：中枢下发的产物包解压到工作目录（zip 内相对路径，
        # 已存在文件按包内版本覆盖；project_context.md 随包落盘）
        if payload.handoff_bundle:
            self._extract_handoff_bundle(ws_dir, payload.handoff_bundle)
        # 权限策略（ADR-061 M2b，Q2）：派发即权威、本地配置为缓存；
        # payload 未带策略时传 None → 沿用本地已有配置（首次则模板最严值）
        effective_policy = self._workspace.ensure_tilacode_config(
            ws_dir, policy=permission_policy.normalize_policy(payload.permission_policy)
        )
        # 前端探针与验证规则（ADR-061 M7）：落 .tilacode/，供子 Agent 做浏览器实测
        self._workspace.ensure_frontend_tooling(ws_dir)
        written: list[Path] = []
        if payload.specification is not None:
            # 三件套版本化目录（ADR-037）：按本轮任务独立保存，不覆盖历史版本
            doc_dir = spec_docs_relative_dir(
                payload.task_id, payload.task_name or payload.description
            )
            written = self._workspace.write_specification_docs(
                ws_dir, payload.specification, doc_dir=doc_dir
            )
        session_id = f"ses_task_{project_key}"
        self._session_directories[session_id] = ws_dir
        self._session_policies[session_id] = effective_policy
        # 轨迹行元数据登记会话归属（ADR-063 M2）；采集器在 execute_task 已创建
        trace_collector = self._trace_collectors.get(payload.task_id)
        if trace_collector is not None:
            trace_collector.configure_session(session_id)
        session_model, actual_model = await self._resolve_session_model(
            payload, workspace_dir=ws_dir
        )
        if session_model is not None:
            # ADR-055 D3 修订（2026-09-27）：tilacode 配置仅在目录 InstanceState
            # 创建时加载，运行中 server 不感知文件变更——写入 provider 后必须
            # 通过 PATCH /config 触发实例失效，create_session 重建实例时才会
            # 加载新 provider；仅影响本目录，不干扰其他目录的并发任务。
            await self._client.invalidate_config(directory=str(ws_dir))
        response = await self._client.create_session(
            session_id=session_id,
            agent=payload.mode,
            directory=str(ws_dir),
            model=session_model,
        )
        if session_model is not None:
            # 派发前预检（ADR-061 C1）——**必须在 create_session 之后**：
            # invalidate 只是把实例标记待销毁，实例（连同其 provider 目录）
            # 在 create_session 重建时才重新加载；此前把预检放在重建之前，
            # 目录 catalog 恒为空，所有带模型的任务必现「模型目录为空」
            # （2026-10-02 用户实测两连败，见 ADR-061 验收 #12 附带）。
            # 预检失败在此 raise → 任务明确失败，依旧避免卡 60 秒后的
            # ModelUnavailableError。
            await self._preflight_session_model(ws_dir, session_model, payload.task_id)
        # 复用 Session 时返回的 agent 可能与请求 mode 不一致，需要显式切换
        data = response.get("data")
        current_agent = data.get("agent") if isinstance(data, dict) else None
        if current_agent and current_agent != payload.mode:
            await self._client.switch_agent(session_id, payload.mode, directory=str(ws_dir))
        # 登记簿 task_id 字段存 project_key：语义为"项目键"（任务链根任务 ID）
        await self._store.upsert(
            project_key, session_id=session_id, workspace_dir=str(ws_dir), status="running"
        )
        # 上报实际执行模型（agent.task_started，ADR-029 增强）：中枢任务详情展示
        await self._safe_callback(
            self._callbacks.on_task_started(payload.task_id, actual_model),
            payload.task_id,
        )
        # 轨迹行（ADR-063 M1）：会话创建成功后上报「启动」（prompt 下发前），
        # 中枢群聊式折叠块据此显示节点已接单、用什么模型、在哪个目录干活
        await self._report_trace_status(
            payload.task_id,
            session_id=session_id,
            stage="启动",
            message="任务已派发到节点",
            extra={"model": actual_model, "directory": str(ws_dir)},
        )
        # 统一输出正斜杠路径（tilacode 运行于 Linux，且便于 prompt 阅读与测试断言）
        doc_paths = [p.relative_to(ws_dir).as_posix() for p in written]
        # 判据防篡改基线（ADR-061 验收 #12 修订 2026-10-03）：必须在 prompt 下发前
        # 拍摄——初版在 gate 启动时才拍，模型可在「工作阶段」提前自建探针
        # （验收②实测形态：模型按需求提示在首轮就写好 check_probe.py，gate 快照
        # 时探针已存在，复查无变化，防线形同虚设）。基线以 project_key 存放，
        # gate 复查时弹出使用；doc_dir 不存在时 snapshot 自然跳过该目录。
        tamper_doc_dir = spec_docs_relative_dir(
            payload.task_id, payload.task_name or payload.description
        )
        self._tamper_baselines[project_key] = verify_tamper.snapshot(ws_dir, tamper_doc_dir)
        return session_id, ws_dir, doc_paths

    def _extract_handoff_bundle(self, ws_dir: Path, bundle: bytes) -> None:
        """解压现场迁移产物包到工作目录（ADR-034，同步 IO，失败仅告警）。

        ADR-055 P2-12 安全加固（复刻 ADR-039 附件策略）：

        - 拒绝绝对路径、``..`` 越界、**反斜杠与 Windows 盘符成员**
          （旧实现只查 ``/`` 前缀与 ``..``，``..\\x``、``C:\\x`` 可穿透）；
        - 条目数与单文件解压后大小上限，防 zip 炸弹；
        - ``namelist()`` 只在 ``with`` 块内使用（旧实现在块外引用）。

        解压失败不阻断任务执行（退化为无现场，行为同旧版本 Agent）。
        """
        try:
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                members = archive.infolist()
                if len(members) > _HANDOFF_MAX_ENTRIES:
                    raise ValueError(f"handoff 包条目数超上限 {_HANDOFF_MAX_ENTRIES}")
                safe_members: list[zipfile.ZipInfo] = []
                for member in members:
                    name = member.filename
                    if not _is_safe_archive_member(name):
                        self._logger.warning(
                            "handoff_bundle_unsafe_member_skipped",
                            extra={"member": name},
                        )
                        continue
                    if member.file_size > _HANDOFF_MAX_MEMBER_BYTES:
                        self._logger.warning(
                            "handoff_bundle_member_too_large_skipped",
                            extra={"member": name, "size": member.file_size},
                        )
                        continue
                    safe_members.append(member)
                for member in safe_members:
                    archive.extract(member, ws_dir)
                file_count = len(safe_members)
            self._logger.info(
                "handoff_bundle_extracted",
                extra={"file_count": file_count},
            )
        except (zipfile.BadZipFile, ValueError) as exc:
            self._logger.warning(
                "handoff_bundle_extract_failed",
                extra={"error": str(exc)},
            )

    async def _resolve_session_model(
        self, payload: TaskDispatchPayload, workspace_dir: Path | None = None
    ) -> tuple[dict[str, str] | None, str]:
        """按派发 payload 解析 session 模型、注入凭据并返回实际模型描述（ADR-029/030/055）。

        - 携带 api_key 且带 base_url：把自定义 provider 定义（baseURL + apiKey +
          模型清单）以**命名空间隔离**方式写入**用户级**配置目录
          （``~/.config/tilacode/tilacode.jsonc``，ADR-055 D3——凭据不落任务
          工作目录），session 使用 ``namespace/model``；实际模型描述为
          ``provider/model``。配置文件方式不依赖 tilacode 内置集成清单——
          任意服务商名（如 aliyun）均可运行（修复：此前把服务商名当
          integration id 调 connect/key，tilacode 清单外 id 直接 500
          UnknownError 导致任务失败）。
        - 携带 api_key 但缺 base_url（旧中枢兼容）：回退 connect/key 全局注入
          （仅对 tilacode 内置集成名有效，如 deepseek）。
        - 缺凭据 / 字段缺失 / 未知 source：回退内置默认免费模型，附 ``降级``
          说明供中枢展示排查。

        Args:
            payload: 任务派发消息。

        Returns:
            (session 模型字典, 实际执行模型描述字符串)。
        """
        selection = payload.executor_model
        if not isinstance(selection, dict):
            return None, _FREE_MODEL_DESC
        source = selection.get("source", "hub")
        if source not in ("hub", "custom"):
            self._logger.warning(
                "executor_model_unknown_source",
                extra={"task_id": payload.task_id, "source": source},
            )
            return None, _FREE_MODEL_DESC
        api_key = selection.get("api_key")
        if not api_key:
            degrade_reason = (
                "中枢未下发凭据，降级" if source == "hub" else "自定义模型未下发凭据，降级"
            )
            self._logger.warning(
                "executor_model_key_missing",
                extra={"task_id": payload.task_id, "source": source},
            )
            return None, f"{_FREE_MODEL_DESC}（{degrade_reason}）"
        provider = str(selection.get("provider") or "deepseek")
        model = str(selection.get("model") or "deepseek-chat")
        base_url = str(selection.get("base_url") or "").strip()
        if base_url:
            namespace = self._workspace.upsert_provider_config(
                provider=provider,
                workspace_dir=workspace_dir,
                model=model,
                base_url=base_url,
                api_key=str(api_key),
                namespace=provider_namespace(provider, payload.task_id),
            )
            return {"providerID": namespace, "id": model}, f"{provider}/{model}"
        # 旧中枢兼容：无 base_url 时按内置集成注入（仅 deepseek 等清单内 id 有效）
        await self._client.ensure_integration_key(provider, str(api_key))
        return {"providerID": provider, "id": model}, f"{provider}/{model}"

    async def _run_session(
        self,
        session_id: str,
        task_id: str,
        prompt: str,
        *,
        directory: str,
        report_progress: bool = True,
        image_parts: list[dict[str, str]] | None = None,
    ) -> ConsumeResult:
        """启动 SSE 消费后下发 prompt 并等待消费结果。

        先订阅后发 prompt 防止订阅建立前事件丢失；订阅重放的历史由
        消费循环 drain 机制排水。``report_progress=False`` 供总结轮使用
        （进度空回调，避免总结阶段进度值回退）；``image_parts`` 为图片
        附件 parts（ADR-039，执行模型支持视觉时非空）。

        ADR-055 P1-9：进度回调透传翻译器产出的执行阶段/当前动作/工具/
        可观测明细，并由 ``_last_progress_report_at`` 记录上报时间供静默期
        心跳去重。
        """

        async def _forward_progress(update: ProgressUpdate) -> None:
            """把翻译器的进度更新转成回调调用（携带 stage/message/tool/detail）。"""
            self._last_progress_report_at[task_id] = time.monotonic()
            self._last_progress_value[task_id] = update.progress
            await self._callbacks.on_progress(
                task_id,
                update.progress,
                update.todos,
                stage=update.stage,
                message=update.message,
                tool=update.tool_name,
                detail=update.detail,
            )

        # 轨迹采集（ADR-063 M2）：消费循环把 reasoning/text/tool 行同步入队到
        # 本任务的采集器；总结轮/自修轮共用同一采集器，seq 连续不重不漏
        trace_collector = self._trace_collectors.get(task_id)
        consumer = SSEConsumer(
            client=self._client,
            task_id=task_id,
            session_id=session_id,
            directory=directory,
            translator=EventTranslator(task_id),
            idle_timeout_seconds=self._config.tilacode_step_idle_timeout_seconds,
            on_progress=_forward_progress if report_progress else _noop_progress,
            on_trace_rows=trace_collector.emit_rows if trace_collector is not None else None,
        )
        self._consumers[task_id] = consumer
        # ADR-055：同一任务会有多轮消费（正常轮 + 总结轮）。创建新消费前先结束
        # 上一轮遗留的消费任务——旧实现只在任务整体结束时取消后台任务，轮次之间
        # 的旧消费协程会成为「僵尸」：继续订阅同一会话的事件流（真实环境下白占
        # 一条 SSE 连接，测试替身下会抢占订阅次序）。
        await self._cancel_previous_consumer(task_id)
        consume_task = asyncio.create_task(consumer.run(), name=f"sse-consume-{task_id}")
        self._consume_tasks[task_id] = consume_task
        try:
            await self._client.send_prompt(
                session_id, prompt, directory=directory, attachments=image_parts
            )
        except BaseException:
            # prompt 下发失败/协程被取消：终止消费任务后原样上抛
            consume_task.cancel()
            await asyncio.gather(consume_task, return_exceptions=True)
            raise
        return await consume_task

    async def _cancel_previous_consumer(self, task_id: str) -> None:
        """取消该任务上一轮遗留的 SSE 消费任务（ADR-055，幂等）。"""
        previous = self._consume_tasks.pop(task_id, None)
        if previous is None or previous.done():
            return
        previous.cancel()
        await asyncio.gather(previous, return_exceptions=True)
        self._logger.info("sse_consume_previous_cancelled", extra={"task_id": task_id})

    async def _file_tree_reporter(self, task_id: str, ws_dir: Path) -> None:
        """执行中周期上报产物文件清单（ADR-028）。

        任何异常仅记录日志，不影响任务执行主链路。
        """
        while True:
            try:
                await self._report_file_tree_once(task_id, ws_dir)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning(
                    "file_tree_report_failed",
                    extra={"task_id": task_id, "error": str(exc)},
                )
            await asyncio.sleep(_FILE_TREE_REPORT_INTERVAL_SECONDS)

    async def _report_file_tree_once(self, task_id: str, ws_dir: Path) -> None:
        """扫描工作目录并上报一次文件清单（ADR-028）。

        ADR-055 P0-1：扫描是同步重 IO（遍历整棵目录树），必须经
        ``asyncio.to_thread`` 执行——直接调用会占住事件循环，导致心跳
        发不出、节点被判离线。
        """
        files, truncated = await asyncio.to_thread(scan_file_tree, ws_dir)
        await self._callbacks.on_file_tree(task_id, files, truncated)

    async def _task_watchdog(self, session_id: str, task_id: str) -> None:
        """无进度看门狗（ADR-055 P0-2 / D1）：识别卡死任务并中断。

        两层判定：

        1. **无进度**（主，默认启用）：``SSEConsumer.last_activity_at`` 停滞超过
           config.task_no_progress_timeout_seconds 即判卡死——任何 SSE 事件
           （含工具调用、压缩、重试）都会重置该时间；
        2. **总时长上限**（可选，默认 0=不限）：payload.timeout_seconds 优先，
           其次 config.task_execution_timeout_seconds。

        命中后尽力 ``interrupt`` 会话并置消费结果为 failed，由 ``execute_task``
        的失败路径上报中枢；槽位随任务结束释放（旧实现卡死会永久占槽）。
        """
        consumer = self._consumers.get(task_id)
        if consumer is None:
            return
        started_at = time.monotonic()
        loop_started = started_at
        no_progress_limit = float(self._config.task_no_progress_timeout_seconds)
        if payload := self._current_tasks.get(task_id):
            total_limit = float(payload.timeout_seconds or 0) or float(
                self._config.task_execution_timeout_seconds
            )
        else:
            total_limit = float(self._config.task_execution_timeout_seconds)
        while True:
            if self._result_of(consumer) is not None:
                return
            await asyncio.sleep(_WATCHDOG_POLL_INTERVAL_SECONDS)
            loop_started = await self._ensure_periodic_pace(loop_started)
            if self._result_of(consumer) is not None:
                return
            now = time.monotonic()
            # 启动停滞快速判定（ADR-055 补丁，2026-09-27）：会话已 admitted 却
            # 迟迟没有任何 step 事件（模型解析失败等前置失败不发事件），
            # 60s 内即给出可操作失败原因，不再干等 30 分钟无进度看门狗。
            if consumer.check_startup_stall(_STARTUP_STALL_TIMEOUT_SECONDS):
                return
            reason: str | None = None
            if now - consumer.last_activity_at > no_progress_limit:
                reason = f"task_no_progress_timeout:{int(no_progress_limit)}"
            elif total_limit > 0 and now - started_at > total_limit:
                reason = f"task_execution_timeout:{int(total_limit)}"
            if reason is None:
                continue
            self._logger.warning(
                "task_timeout",
                extra={
                    "event": "task_timeout",
                    "task_id": task_id,
                    "session_id": session_id,
                    "reason": reason,
                    "idle_seconds": round(now - consumer.last_activity_at, 1),
                    "elapsed_seconds": round(now - started_at, 1),
                },
            )
            try:
                await self._client.interrupt(session_id)
            except TilacodeAPIError as exc:
                self._logger.warning(
                    "task_timeout_interrupt_failed",
                    extra={"task_id": task_id, "session_id": session_id, "error": str(exc)},
                )
            consumer.fail(reason)
            return

    async def _progress_heartbeat(self, task_id: str) -> None:
        """静默期进度心跳（ADR-055 P2-13）。

        单个 step 内长时间无事件（长思考/长命令/上下文压缩）时，前端会看起来
        卡死。本任务在静默超过 ``agent_progress_heartbeat_seconds`` 时补发一次
        进度（progress 值不变、message 标注「仍在执行：<最近动作>」），使面板
        持续可见活动。
        """
        interval = float(self._config.agent_progress_heartbeat_seconds)
        loop_started = time.monotonic()
        while True:
            await asyncio.sleep(interval)
            loop_started = await self._ensure_periodic_pace(loop_started)
            # 轨迹兜底 flush（ADR-063 M2）：静默期没有新事件时，积压行与
            # 上报失败的重试批次也至少每个心跳周期获得一次发送机会
            trace_collector = self._trace_collectors.get(task_id)
            if trace_collector is not None:
                await trace_collector.flush()
            consumer = self._consumers.get(task_id)
            if consumer is None or consumer.has_result:
                return
            now = time.monotonic()
            if now - consumer.last_activity_at < interval:
                continue  # 有真实事件流动，无需补发
            if now - self._last_progress_report_at.get(task_id, 0.0) < interval:
                continue
            translator = consumer.translator
            stage = translator.stage
            message = translator.message or "任务执行中"
            self._last_progress_report_at[task_id] = now
            await self._safe_callback(
                self._callbacks.on_progress(
                    task_id,
                    translator.progress,
                    translator.todos,
                    stage=stage,
                    message=f"仍在执行：{message}",
                    tool=translator.tool_name,
                ),
                task_id,
            )

    @staticmethod
    async def _ensure_periodic_pace(loop_started: float) -> float:
        """保证周期任务两轮之间至少有 ``_PERIODIC_MIN_SLEEP_SECONDS`` 真实间隔。

        周期任务（看门狗/进度心跳）靠 ``asyncio.sleep(长间隔)`` 节流；当该函数
        被替换为「零延迟让出」时（测试替身或异常运行时），循环会以微秒级速度
        空转并持续抢占就绪队列，把真正干活的 SSE 消费任务饿死。这里按真实耗时
        补偿等待，使节流不依赖 ``asyncio.sleep`` 的实现。

        Args:
            loop_started: 上一轮开始时的 ``time.monotonic()``。

        Returns:
            本轮开始时间，供下一轮使用。
        """
        now = time.monotonic()
        elapsed = now - loop_started
        if elapsed < _PERIODIC_MIN_SLEEP_SECONDS:
            await asyncio.sleep(_PERIODIC_MIN_SLEEP_SECONDS - elapsed)
            now = time.monotonic()
        return now

    @staticmethod
    def _result_of(consumer: SSEConsumer) -> ConsumeResult | None:
        """读取消费循环当前结果（None 表示仍在执行）。"""
        return consumer.result

    async def handle_file_fetch(
        self, request_id: str, task_id: str, mode: str, path: str | None
    ) -> None:
        """处理中枢产物拉取指令（ADR-028）：读文件或打包目录后分块回传。

        Args:
            request_id: 中枢侧拉取请求 ID。
            task_id: 任务 ID（据此反查项目键与工作目录）。
            mode: file（单文件）| zip（整目录打包）。
            path: file 模式下的工作目录内相对路径。
        """
        try:
            # 以执行时的相同参数反查工作目录：project_key + output_dir
            # （缺一不可——ADR-036 显式工作目录任务若不带 output_dir 会
            # 解析到默认空目录，所有按需拉取恒报文件不存在）。
            # 反查顺序：内存映射 → 持久化登记簿（Agent 重启后内存映射
            # 丢失，2026-09-23）→ 默认解析兜底
            project_key, output_dir = self._task_project_keys.get(task_id, (task_id, None))
            if task_id not in self._task_project_keys and self._mapping_store is not None:
                persisted = await self._mapping_store.get(task_id)
                if persisted is not None:
                    project_key, output_dir = persisted
            # 会话登记簿按 project_key 索引，持有执行时实际工作目录（绝对
            # 路径），直接使用以避免 prepare 重解析偏差
            record = await self._store.get(project_key)
            if record is not None and record.workspace_dir:
                ws_dir = Path(record.workspace_dir)
            else:
                ws_dir = self._workspace.prepare(project_key, output_dir=output_dir)
            if mode == "zip":
                payload = self._pack_workspace(ws_dir)
            else:
                relative = _sanitize_fetch_path(path)
                target = ws_dir / relative
                if not target.resolve().is_relative_to(ws_dir.resolve()) or not target.is_file():
                    raise FileNotFoundError(f"文件不存在: {path}")
                payload = target.read_bytes()
        except Exception as exc:
            await self._safe_callback(self._callbacks.on_file_error(request_id, str(exc)), task_id)
            return

        total = max(1, -(-len(payload) // _FILE_CHUNK_SIZE))
        for seq in range(total):
            chunk = payload[seq * _FILE_CHUNK_SIZE : (seq + 1) * _FILE_CHUNK_SIZE]
            await self._safe_callback(
                self._callbacks.on_file_chunk(
                    request_id,
                    seq,
                    total,
                    base64.b64encode(chunk).decode("ascii"),
                    seq == total - 1,
                ),
                task_id,
            )
        self._logger.info(
            "file_fetch_served",
            extra={
                "request_id": request_id,
                "task_id": task_id,
                "mode": mode,
                "size": len(payload),
            },
        )

    def _pack_workspace(self, ws_dir: Path) -> bytes:
        """将工作目录打包为 zip 字节流（复用 file_scanner 排除规则）。

        Raises:
            ValueError: 打包产物超出 500MB 上限。
        """
        files, _truncated = scan_file_tree(ws_dir)
        buffer = io.BytesIO()
        total = 0
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in files:
                target = ws_dir / item["path"]
                archive.write(target, item["path"])
                total += item["size"]
                if total > _MAX_PACK_BYTES:
                    raise ValueError("工作目录超出打包上限（500MB）")
        return buffer.getvalue()

    async def _dispatch_result(
        self,
        payload: TaskDispatchPayload,
        session_id: str,
        ws_dir: Path,
        result: ConsumeResult,
    ) -> None:
        """根据消费结果更新登记簿并触发完成/失败回调。

        plan 模式完成时读取工作目录 plans 产物全文；build 模式完成时向
        同一 Session 追加总结 prompt 生成本轮修改说明，均随完成回调上报
        中枢（见 ADR-012 plan 审批流与 change_summary 增补决策）。
        """
        task_id = payload.task_id
        project_key = payload.base_task_id or task_id
        if result.status == "completed":
            # 完成闸（ADR-061 M4）：必过层全绿才允许上报 completed；
            # 红/无法验证先自修（≤5 轮），仍不过则上报「等用户决定」
            report = await self._run_completion_gate(payload, session_id, ws_dir, project_key)
            if report is None:
                await self._store.update_status(project_key, "verify_blocked")
                return
            await self._store.update_status(project_key, "completed")
            # 轨迹行（ADR-063 M1）：任务成功完成分支上报「结束」行（旁路，失败不影响任务）
            await self._report_trace_status(
                task_id,
                session_id=session_id,
                stage="结束",
                message="会话已结束",
            )
            if payload.mode == "plan":
                plan_content = self._workspace.read_plan_documents(ws_dir)
                change_summary = None
            else:
                plan_content = None
                change_summary = await self._generate_change_summary(
                    session_id, task_id, directory=str(ws_dir)
                )
            # 项目上下文记忆（ADR-034）：读取 AI 在任务中更新的 project_context.md
            project_context: str | None = None
            context_path = ws_dir / "project_context.md"
            if context_path.is_file():
                try:
                    project_context = context_path.read_text(encoding="utf-8")[:200_000]
                except OSError:
                    self._logger.warning("project_context_read_failed", extra={"task_id": task_id})
            await self._report_file_tree_once(task_id, ws_dir)
            self._logger.info(
                "task_bridge_completed",
                extra={
                    "task_id": task_id,
                    "session_id": session_id,
                    "plan_reported": plan_content is not None,
                    "summary_reported": change_summary is not None,
                    "context_reported": project_context is not None,
                },
            )
            await self._safe_callback(
                # 上报中枢的会话标识使用项目键（UUID）：中枢 Task.session_id 为
                # UUID 列，携带 ses_task_ 前缀的完整 id 会因格式不符被丢弃
                self._callbacks.on_completed(
                    task_id,
                    project_key,
                    plan_content,
                    change_summary,
                    project_context,
                    verify_report=report.to_payload(),
                ),
                task_id,
            )
        else:
            await self._dispatch_failure(task_id, result.reason or "unknown", project_key)

    async def _run_completion_gate(
        self,
        payload: TaskDispatchPayload,
        session_id: str,
        ws_dir: Path,
        project_key: str,
    ) -> verify_runner.VerifyReport | None:
        """执行完成闸（ADR-061 M4）。

        Args:
            payload: 任务派发消息（含规格与判据）。
            session_id: 当前会话 ID（自修需要在同一会话回灌）。
            ws_dir: 任务工作目录（判据与命令在此执行）。
            project_key: 任务链根任务 ID（阻断上报用）。

        Returns:
            报告对象 = **放行**（必过层全绿）；``None`` = **阻断**（已上报中枢等用户决定）。

        语义：
        - 必过检查全绿 → 放行；
        - 有必过项红/超时 → **自修 ≤5 轮**（同会话回灌失败命令 + 输出尾部）后复验，全绿即放行；
        - **无判据（unverifiable）→ 不自修、直接阻断**：代码修不出「判据」本身，
          按用户已定「无法验证不得呈现为正常完成」，交由中枢在对话区找用户决定。
        """
        doc_dir = spec_docs_relative_dir(
            payload.task_id, payload.task_name or payload.description
        )
        # 判据防篡改基线（ADR-061 验收 #12 修订 2026-10-03）：优先使用 _prepare_session
        # 在 prompt 下发前拍摄的基线（覆盖「工作阶段自建探针」形态）；缺失时回退为
        # gate 启动时现拍（旧路径兼容：会话复用/重派未走 prepare 的场景）。
        # 每轮 passed 后复查——被模型改写或新建的探针不得呈现为正常完成。
        tamper_baseline = self._tamper_baselines.pop(project_key, None)
        if tamper_baseline is None:
            tamper_baseline = verify_tamper.snapshot(ws_dir, doc_dir)
        plan, source = verify_runner.load_plan(ws_dir, doc_dir)
        report = await verify_runner.run_plan(ws_dir, plan, source)
        self._log_verify_result(payload.task_id, "initial", report)
        if report.status == "passed":
            tampered = verify_tamper.changed_paths(ws_dir, doc_dir, tamper_baseline)
            if tampered:
                return await self._block_on_tampered_evidence(
                    payload, project_key, report, tampered
                )
            return report
        if report.status == "unverifiable":
            await self._report_verify_blocked(payload.task_id, project_key, report, rounds=0)
            return None

        for round_index in range(1, VERIFY_MAX_REPAIR_ROUNDS + 1):
            prompt = self._build_repair_prompt(report, round_index)
            try:
                await self._run_session(
                    session_id,
                    payload.task_id,
                    prompt,
                    directory=str(ws_dir),
                    report_progress=False,
                )
            except (TilacodeAPIError, TaskBridgeError, SessionStoreError, OSError) as exc:
                self._logger.warning(
                    "verify_repair_prompt_failed",
                    extra={
                        "event": "verify_repair_prompt_failed",
                        "task_id": payload.task_id,
                        "round": round_index,
                        "error": str(exc),
                    },
                )
                break
            report = await verify_runner.run_plan(ws_dir, plan, source)
            self._log_verify_result(payload.task_id, f"repair_{round_index}", report)
            if report.status == "passed":
                tampered = verify_tamper.changed_paths(ws_dir, doc_dir, tamper_baseline)
                if tampered:
                    return await self._block_on_tampered_evidence(
                        payload, project_key, report, tampered
                    )
                return report

        await self._report_verify_blocked(
            payload.task_id, project_key, report, rounds=VERIFY_MAX_REPAIR_ROUNDS
        )
        return None

    async def _block_on_tampered_evidence(
        self,
        payload: TaskDispatchPayload,
        project_key: str,
        report: verify_runner.VerifyReport,
        tampered: list[str],
    ) -> None:
        """判据/探针被修改后即使全部通过也不放行：按无法验证阻断（M4 #12）。"""
        listing = "\n".join(f"- {item}" for item in tampered)
        self._logger.warning(
            "verify_gate_tampered",
            extra={
                "event": "verify_gate_tampered",
                "task_id": payload.task_id,
                "files": tampered,
            },
        )
        blocked = verify_runner.VerifyReport(
            status="unverifiable",
            source=report.source,
            checks=report.checks,
            note=(
                "⚠️ 验收判据/探针文件在验收过程中被修改，检查结果不可信：\n"
                f"{listing}\n"
                "如需继续，请恢复判据文件原始内容后重新验收，或人工确认修改合理。"
            ),
        )
        await self._report_verify_blocked(payload.task_id, project_key, blocked, rounds=0)
        return None

    def _log_verify_result(
        self, task_id: str, phase: str, report: verify_runner.VerifyReport
    ) -> None:
        """记录一次验收结果（阶段/状态/必过失败数）。"""
        self._logger.info(
            "verify_gate_result",
            extra={
                "event": "verify_gate_result",
                "task_id": task_id,
                "phase": phase,
                "status": report.status,
                "source": report.source,
                "checks": len(report.checks),
                "must_pass_failures": len(report.must_pass_failures),
            },
        )

    @staticmethod
    def _build_repair_prompt(report: verify_runner.VerifyReport, round_index: int) -> str:
        """构造自修提示：逐条列出未过必过项的命令、状态与输出尾部。"""
        blocks: list[str] = []
        for item in report.must_pass_failures:
            tail = item.output_tail.strip() or "（无输出）"
            blocks.append(
                f"【{item.layer} / {item.name}】\n"
                f"命令：{item.cmd}\n"
                f"结果：{item.status}（退出码 {item.exit_code}）\n"
                f"输出尾部：\n{tail}"
            )
        joined = "\n\n".join(blocks) or "（无具体失败项）"
        return (
            f"{_VERIFY_REPAIR_PROMPT_HEADER}"
            f"（第 {round_index}/{VERIFY_MAX_REPAIR_ROUNDS} 轮）\n\n{joined}"
            f"{_VERIFY_REPAIR_PROMPT_TAILER}"
        )

    async def _report_verify_blocked(
        self,
        task_id: str,
        project_key: str,
        report: verify_runner.VerifyReport,
        *,
        rounds: int,
    ) -> None:
        """上报验收阻断（中枢据此进入 verify_blocked 并找用户决定）。"""
        payload = report.to_payload()
        payload["repair_rounds"] = rounds
        payload["summary"] = report.summary()
        self._logger.warning(
            "verify_gate_blocked",
            extra={
                "event": "verify_gate_blocked",
                "task_id": task_id,
                "status": report.status,
                "repair_rounds": rounds,
                "must_pass_failures": len(report.must_pass_failures),
            },
        )
        await self._safe_callback(
            self._callbacks.on_verify_blocked(task_id, project_key, payload),
            task_id,
        )

    async def _generate_change_summary(
        self, session_id: str, task_id: str, *, directory: str
    ) -> str | None:
        """向同一 Session 追加总结 prompt 生成本轮修改说明。

        复用 SSE 消费循环等待总结输出完成，再从 session history 提取最后
        一条 assistant 文本；任何失败仅记 warning 并返回 None，不阻断
        任务完成上报（降级决策见 ADR-012 change_summary 增补决策）。
        """
        try:
            result = await self._run_session(
                session_id,
                task_id,
                _SUMMARY_PROMPT,
                directory=directory,
                report_progress=False,
            )
        except (TilacodeAPIError, TaskBridgeError, SessionStoreError, OSError) as exc:
            self._logger.warning(
                "change_summary_prompt_failed",
                extra={"task_id": task_id, "error": str(exc)},
            )
            return None
        if result.status != "completed":
            self._logger.warning(
                "change_summary_not_completed",
                extra={"task_id": task_id, "reason": result.reason},
            )
            return None
        return await self._extract_last_assistant_text(session_id, task_id, directory=directory)

    async def _extract_last_assistant_text(
        self, session_id: str, task_id: str, *, directory: str
    ) -> str | None:
        """从会话上下文提取最后一条 assistant 消息的文本（ADR-055 P0-5）。

        取数顺序：

        1. ``GET /api/session/{id}/context`` —— 返回 **当前上下文消息数组**
           （``SessionMessage.Message[]``，含 ``role``/``content``），是真实
           语义匹配的端点；
        2. 回退 ``GET /api/session/{id}/history`` —— 返回的是 **durable 事件
           分页** ``{data: SessionEvent.Durable[], hasMore}``；旧实现把事件数组
           当消息数组解析（找 ``role``/``parts``），必然取不到文本，
           导致 ``change_summary`` 恒为空。此处按事件结构从
           ``session.next.text.ended`` 事件里抽取文本兜底。

        防御式解析：结构不符时逐条跳过；无可用文本时返回 None。
        """
        messages = await self._fetch_context_messages(session_id, task_id, directory=directory)
        if messages is not None:
            text = _last_assistant_text_from_messages(messages)
            if text:
                return text
        events = await self._fetch_history_events(session_id, task_id, directory=directory)
        if events is not None:
            text = _last_assistant_text_from_events(events)
            if text:
                return text
        self._logger.warning("change_summary_no_assistant_text", extra={"task_id": task_id})
        return None

    async def _fetch_context_messages(
        self, session_id: str, task_id: str, *, directory: str
    ) -> list[dict[str, Any]] | None:
        """拉取会话上下文消息数组；端点不可用或失败时返回 None。"""
        get_context = getattr(self._client, "get_context", None)
        if get_context is None:
            return None
        try:
            return await get_context(session_id, directory=directory)
        except TilacodeAPIError as exc:
            self._logger.warning(
                "change_summary_context_failed",
                extra={"task_id": task_id, "error": str(exc)},
            )
            return None

    async def _fetch_history_events(
        self, session_id: str, task_id: str, *, directory: str
    ) -> list[dict[str, Any]] | None:
        """拉取会话 durable 事件分页（回退路径）；失败时返回 None。"""
        try:
            return await self._client.get_history(session_id, directory=directory)
        except TilacodeAPIError as exc:
            self._logger.warning(
                "change_summary_history_failed",
                extra={"task_id": task_id, "error": str(exc)},
            )
            return None

    async def _dispatch_failure(self, task_id: str, reason: str, project_key: str) -> None:
        """登记失败状态并触发失败回调；登记簿按 project_key 更新。"""
        try:
            await self._store.update_status(project_key, "failed")
        except SessionStoreError as exc:
            self._logger.warning(
                "task_bridge_store_update_failed", extra={"task_id": task_id, "error": str(exc)}
            )
        self._logger.warning("task_bridge_failed", extra={"task_id": task_id, "reason": reason})
        await self._safe_callback(self._callbacks.on_failed(task_id, reason), task_id)

    async def _safe_callback(self, callback: Awaitable[None], task_id: str) -> None:
        """执行上报回调；回调异常仅记日志避免影响任务终态。"""
        try:
            await callback
        except Exception as exc:
            self._logger.error(
                "task_bridge_callback_failed", extra={"task_id": task_id, "error": str(exc)}
            )

    async def _report_trace_status(
        self,
        task_id: str,
        *,
        session_id: str | None,
        stage: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """上报一条 ``status`` 轨迹行（ADR-063 M1/M2）。

        seq 由任务采集器统一分配（与 reasoning/text/tool 行共用单调序列）。
        入队后立即 flush 保证启动/结束行即时可见；上报是**旁路**：
        异常只记 warning，绝不上抛、绝不影响任务主链路。
        """
        collector = self._trace_collectors.get(task_id)
        if collector is None:
            return
        collector.emit_status(stage=stage, message=message, extra=extra)
        try:
            await collector.flush()
        except Exception as exc:  # noqa: BLE001 — 旁路上报，失败绝不影响任务
            self._logger.warning(
                "agent_trace_report_failed",
                extra={"task_id": task_id, "error": str(exc)},
            )

    def _persist_attachments(self, ws_dir: Path, payload: TaskDispatchPayload) -> list[Path]:
        """任务附件落盘到工作目录 ``.hub-attachments/``（ADR-039）。

        图片/普通文件直接写入；zip 压缩包（kind=archive）校验后解压到
        同名子目录（P2）。同步 IO；单个附件失败仅告警跳过，不阻断任务。
        """
        blobs = payload.attachment_blobs or []
        if not blobs:
            return []
        attachment_dir = ws_dir / ".hub-attachments"
        saved: list[Path] = []
        for info, data in blobs:
            safe_name = Path(info.file_name).name or "attachment"
            # 派发 payload 无 kind 字段：按 mime 识别压缩包（与 hub 白名单一致）
            if info.mime in ("application/zip", "application/x-zip-compressed"):
                stem = safe_name[:-4] if safe_name.lower().endswith(".zip") else safe_name
                target_dir = attachment_dir / f"{info.id[:8]}_{stem}"
                extracted = self._extract_attachment_archive(info, data, target_dir)
                if extracted is not None:
                    saved.append(extracted)
                continue
            target = attachment_dir / f"{info.id[:8]}_{safe_name}"
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                saved.append(target)
            except OSError as exc:
                self._logger.warning(
                    "task_attachment_persist_failed",
                    extra={"task_id": payload.task_id, "attachment_id": info.id, "error": str(exc)},
                )
        if saved:
            self._logger.info(
                "task_attachments_persisted",
                extra={"task_id": payload.task_id, "count": len(saved)},
            )
        return saved

    def _extract_attachment_archive(self, info: Any, data: bytes, target_dir: Path) -> Path | None:
        """解压附件 zip 到目标目录（zip slip 防护，ADR-039 P2）。

        安全校验：拒绝绝对路径/反斜杠/``..`` 越界成员；单成员解压后
        ≤200MB；条目数 ≤5000（防 zip 炸弹）。校验失败删除半成品目录并
        返回 None（不阻断任务执行，prompt 由调用方按剩余附件说明）。
        """
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                if len(members) > _ARCHIVE_MAX_ENTRIES:
                    raise ValueError(f"条目数超上限 {_ARCHIVE_MAX_ENTRIES}")
                for member in members:
                    name = member.filename
                    if name.startswith("/") or "\\" in name or ".." in name.split("/"):
                        raise ValueError(f"包含越界路径成员：{name}")
                    if member.file_size > _ARCHIVE_MAX_MEMBER_BYTES:
                        raise ValueError(f"内含单文件过大：{name}")
                target_dir.mkdir(parents=True, exist_ok=True)
                archive.extractall(target_dir)
            self._logger.info(
                "task_attachment_archive_extracted",
                extra={
                    "attachment_id": getattr(info, "id", ""),
                    "members": len(members),
                },
            )
            return target_dir
        except (zipfile.BadZipFile, ValueError, OSError) as exc:
            self._logger.warning(
                "task_attachment_archive_rejected",
                extra={
                    "attachment_id": getattr(info, "id", ""),
                    "error": str(exc),
                },
            )
            shutil.rmtree(target_dir, ignore_errors=True)
            return None

    def _build_image_parts(self, payload: TaskDispatchPayload) -> list[dict[str, str]]:
        """执行模型支持视觉时把图片附件转为 tilacode FileParts（ADR-039）。

        执行模型未带 ``supports_vision`` 标记时返回空列表——图片仅落盘，
        由 prompt 文本注明路径（不支持图片的模型无法看图，避免 API 报错）。
        """
        selection = payload.executor_model or {}
        if not selection.get("supports_vision"):
            if payload.attachments:
                self._logger.warning(
                    "task_attachment_image_parts_skipped",
                    extra={
                        "task_id": payload.task_id,
                        "reason": "执行模型不支持图片输入，附件仅落盘",
                        "attachment_count": len(payload.attachments),
                    },
                )
            return []
        parts: list[dict[str, str]] = []
        for info, data in payload.attachment_blobs or []:
            if not info.mime.startswith("image/"):
                continue
            encoded = base64.b64encode(data).decode("ascii")
            parts.append(
                {
                    "type": "file",
                    "mime": info.mime,
                    "filename": info.file_name,
                    "url": f"data:{info.mime};base64,{encoded}",
                }
            )
        return parts

    @staticmethod
    def _environment_prompt_part(payload: TaskDispatchPayload, ws_dir: Path) -> str | None:
        """构建环境要求 prompt 块（ADR-048 P2）。

        - 任务无环境要求清单：返回 None；
        - 工作目录已存在 ``.tilacode/environment.md``（本项目此前已检查过，
          同项目迭代/同节点重跑）：提示环境已就绪，禁止重复探测；
        - 不存在（项目首次开发，或改派到新节点的新工作目录）：指示读取并
          执行 ``.tilacode/rules/environment-check.md``（中枢随元规则下发，
          含本次环境清单与处理规则），并内联清单兜底（文档缺失时仍可执行）。
        """
        requirements = payload.environment_requirements
        if not requirements:
            return None
        env_md = ws_dir / ".tilacode" / "environment.md"
        if env_md.is_file():
            return (
                "本工作目录的环境已在此前任务中检查并安装就绪"
                "（记录见 .tilacode/environment.md）。"
                "无需重复探测或安装环境，直接按上述要求开工；"
                "如开发中发现缺少清单外的依赖，正常安装即可。"
            )
        lines = [
            "## 开工第一步：环境检查与安装",
            "",
            "请先读取并执行 `.tilacode/rules/environment-check.md`"
            "（中枢下发的环境检查规则，含本次环境要求清单与处理规则）；"
            "该文档缺失时，按以下清单执行：",
            "",
        ]
        for req in requirements:
            line = f"- **{req.name}**：运行 `{req.check_cmd}`"
            if req.min_version:
                line += f"，要求版本 ≥ {req.min_version}"
            lines.append(line)
            if req.install_hint:
                lines.append(f"  - 缺失时的安装提示：{req.install_hint}")
        lines.extend(
            [
                "",
                "处理规则：仅项目首次开发时执行；已存在且版本满足 → 记录实际版本；"
                "缺失或版本过低 → 自行选择合适方式安装后复查通过；全部就绪后把结果"
                "写入 `.tilacode/environment.md`（每项一行：环境名、实际版本、状态），"
                "供本项目后续迭代跳过检查；某项环境确实无法安装时，不要静默跳过，"
                "在进度上报中说明缺什么、为什么。",
            ]
        )
        return "\n".join(lines)

    def _build_prompt(
        self,
        payload: TaskDispatchPayload,
        doc_paths: list[str],
        ws_dir: Path,
        attachment_paths: list[Path] | None = None,
    ) -> str:
        """组装下发 prompt：任务描述 + 文档说明（按任务链场景分支）+ 模式提示。

        任务链场景（base_task_id 存在）下项目代码已在工作目录，prompt
        聚焦本轮增量修改，避免 AI 重复开发已有功能（见 ADR-012 迭代链决策）。
        现场迁移（ADR-034）：工作目录存在 project_context.md（上一 Agent 的
        项目上下文记忆）时，要求 AI 先阅读再执行；并要求任务完成前更新该
        文件，保证链上上下文持续最新。
        任务附件（ADR-039）：prompt 中注明附件保存路径供 AI 结合理解需求。
        """
        parts: list[str] = [payload.description or payload.task_name or "请完成指定开发任务。"]
        # 待办纪律（2026-10-07 用户实测：纪律放 prompt 末尾对 deepseek 约束力
        # 不足，子 Agent 到任务 90% 才第一次调用 todo 工具——前移到**最前面**
        # 并给出明确的第一动作，LLM 对开篇指令的遵守度显著更高）：
        # todo 工具的状态变化会实时上报中枢展示给用户，是唯一的进度来源。
        parts.append(
            "【执行纪律——你的第一个动作】在读取任何文件/执行任何命令之前，"
            "先调用 todo 工具把本任务拆成 3~8 项计划清单；"
            "此后每开始一项先将其标记为进行中，做完立即标记为已完成"
            "（状态一变化就调用 todo 工具，不要攒到最后批量更新）；"
            "任务收尾前确认清单中没有遗留的未完成项。"
        )
        # 环境要求块（ADR-048 P2）：紧随任务描述，首次检查/安装或已就绪提示
        environment_part = TaskBridge._environment_prompt_part(payload, ws_dir)
        if environment_part:
            parts.append(environment_part)
        if attachment_paths:
            names = "、".join(path.name for path in attachment_paths)
            parts.append(
                f"用户为本任务上传了 {len(attachment_paths)} 个附件（{names}），"
                "已保存到工作目录 .hub-attachments/ 下（zip 压缩包已解压到同名目录），"
                "请先浏览附件内容，结合附件理解需求。"
            )
        context_path = ws_dir / "project_context.md"
        if context_path.is_file():
            parts.append(
                "工作目录下存在 project_context.md（此前执行该项目任务的 AI 留下的"
                "项目上下文记忆：项目目标/目录结构/已完成/进行中/注意事项）。"
                "请先完整阅读该文件了解项目背景，再开始本轮任务，避免重复或冲突。"
            )
        if payload.base_task_id is not None:
            if payload.change_brief:
                if doc_paths:
                    # 本轮随派发重新生成了三件套（ADR-037 版本目录）
                    joined = "、".join(doc_paths)
                    parts.append(
                        f"该项目的代码已存在于当前工作目录。本轮任务：{payload.change_brief}。"
                        f"本轮需求文档已更新至 {joined}，历史版本保留在 specs/ 其他目录，"
                        "请先阅读本轮文档了解全貌，再只实施本轮要求的修改，"
                        "不要重复开发已有功能。"
                    )
                else:
                    # 本轮未随派发下发文档（简单增量修改，ADR-036 按需生成）
                    parts.append(
                        f"该项目的代码已存在于当前工作目录。本轮任务：{payload.change_brief}。"
                        "本轮未重新生成需求文档，历史文档见 specs/ 各目录"
                        "（多版本按目录区分），请以代码现状与本轮要求为准，"
                        "不要重复开发已有功能。"
                    )
            else:
                parts.append(
                    "该项目代码已存在，历史需求文档见 specs/ 目录"
                    "（多版本按目录区分），请阅读最新版本文档后继续完善。"
                )
        elif doc_paths:
            joined = "、".join(doc_paths)
            parts.append(f"工作目录下有需求文档（{joined}），请先阅读后按要求完成开发。")
        if payload.mode == "plan":
            parts.append(
                "当前为规划模式，请产出方案文档到 .tilacode/plans/ 目录，不要修改业务代码。"
            )
        elif payload.mode == "build":
            parts.append(
                "任务完成前，请将本项目的最新状态写入工作目录下 project_context.md"
                "（若无则创建），内容包含：项目目标、目录结构、已完成事项、进行中/"
                "待办事项、如何继续开发、注意事项——供后续接手的开发者或 AI 快速"
                "了解项目全貌（ADR-034 项目上下文记忆）。"
            )
        return "\n\n".join(parts)

    async def _task_event_pump(self, task_id: str, ws_dir: Path) -> None:
        """任务级实例事件泵：以任务工作目录订阅实例级 SSE。

        tilacode 实例级流按 ``x-tilacode-directory`` 目录严格过滤事件
        （``event.location.directory === 订阅目录``）；任务事件
        （``todo.updated``、权限兜底）的目录为任务工作目录，全局根目录
        订阅会被过滤丢弃，因此每个任务建立一条任务目录级订阅，随任务
        结束取消。断流后按固定间隔重连直至任务结束。
        """
        while True:
            # 观测（2026-10-06 子 Agent 待办断链排查）：统计本连接收到的事件类型，
            # 断连时汇总输出；todo.updated 逐条打点——此前链路静默无迹可循。
            seen_types: dict[str, int] = {}
            try:
                async for event in self._client.stream_instance_events(directory=str(ws_dir)):
                    event_type = event.get("type") if isinstance(event, dict) else None
                    if isinstance(event_type, str):
                        seen_types[event_type] = seen_types.get(event_type, 0) + 1
                    if is_permission_event(event):
                        await self._handle_permission_event(event)
                    elif event_type == _EVENT_TODO_UPDATED:
                        self._logger.info(
                            "instance_stream_todo_event",
                            extra={"task_id": task_id, "event": str(event)[:400]},
                        )
                        await self._handle_todo_updated(event)
            except TilacodeAPIError as exc:
                self._logger.warning(
                    "task_event_pump_stream_failed",
                    extra={"task_id": task_id, "error": str(exc)},
                )
            self._logger.info(
                "instance_stream_reconnected",
                extra={"task_id": task_id, "event_types": seen_types},
            )
            await asyncio.sleep(_PERMISSION_PUMP_RECONNECT_SECONDS)

    def _instance_event_payload(self, event: dict[str, Any]) -> dict[str, Any]:
        """提取实例级 SSE 事件 payload。

        tilacode 实例级流使用 ``properties`` 字段承载事件数据（Session 级流
        使用 ``data``），优先读取 ``properties``，缺失时回退到 ``data``。
        """
        payload = event.get("properties")
        if isinstance(payload, dict):
            return payload
        payload = event.get("data")
        return payload if isinstance(payload, dict) else {}

    def _session_policy(self, session_id: str, ws_dir: Path | None) -> dict[str, Any]:
        """取会话实际生效的权限策略（ADR-061 M2b）。

        内存优先；内存无记录（节点重启后复用目录、或会话由别的任务建立）时
        **回读工作区配置**——配置文件本身即策略缓存（Q2）。两者都没有时用最严默认值。
        """
        cached = self._session_policies.get(session_id)
        if cached is not None:
            return cached
        if ws_dir is not None:
            return self._workspace.ensure_tilacode_config(ws_dir)
        return permission_policy.default_policy()

    async def _handle_permission_event(self, event: dict[str, Any]) -> None:
        """按中枢下发的权限策略回复权限请求（ADR-061 M2b，Q3）。

        命中白名单 → ``once``（仅本次允许）；否则 ``reject`` + **可读原因**。

        **宁可多拒不可少拒**：此前实现是无条件 ``once``，等于把 tilacode 的
        「工作区外 deny / ``.env`` 拒绝」等护栏全部绕过；现在 ``action`` 或
        ``resources`` 缺失/不可评估时一律拒绝（fail-closed）。
        """
        if not is_permission_event(event):
            return
        data = self._instance_event_payload(event)
        request_id = data.get("id")
        session_id = data.get("sessionID")
        if not request_id or not session_id:
            self._logger.warning(
                "permission_event_missing_fields",
                extra={"session_id": session_id, "request_id": request_id},
            )
            return
        ws_dir = self._session_directories.get(str(session_id))
        policy = self._session_policy(str(session_id), ws_dir)

        action = data.get("action")
        raw_resources = data.get("resources")
        resources = (
            [item for item in raw_resources if isinstance(item, str)]
            if isinstance(raw_resources, list)
            else []
        )
        # 全部资源都要命中白名单才算允许（与 tilacode「任一 deny 即 deny」的求值一致）
        evaluable = (
            isinstance(action, str)
            and bool(resources)
            and len(resources) == len(raw_resources)
        )
        allowed = evaluable and all(
            permission_policy.is_allowed(policy, action, resource) for resource in resources
        )
        reply = "once" if allowed else "reject"
        message = (
            None
            if allowed
            else permission_policy.deny_reason(
                str(action) if isinstance(action, str) else "<未知动作>",
                ", ".join(resources) if resources else "<未知资源>",
            )
        )
        try:
            await self._client.permission_reply(
                str(session_id),
                str(request_id),
                reply,
                message=message,
                directory=str(ws_dir) if ws_dir else None,
            )
            self._logger.info(
                "permission_auto_replied" if allowed else "permission_auto_rejected",
                extra={
                    "session_id": session_id,
                    "request_id": request_id,
                    "permission_action": action if isinstance(action, str) else None,
                    "permission_resources": resources,
                    "permission_mode": policy.get("mode"),
                    "allow_count": len(policy.get("allow") or []),
                    "evaluable": evaluable,
                },
            )
        except TilacodeAPIError as exc:
            self._logger.warning(
                "permission_reply_failed",
                extra={
                    "session_id": session_id,
                    "request_id": request_id,
                    "error": str(exc),
                },
            )

    async def _handle_todo_updated(self, event: dict[str, Any]) -> None:
        """将实例级 ``todo.updated`` 事件转发给当前执行中的 SSEConsumer。

        仅当事件携带的 ``sessionID`` 与某个在跑 consumer 的 session_id 匹配时才
        转发，避免同一工作目录下其他 session 的待办列表串扰。
        """
        data = self._instance_event_payload(event)
        session_id = data.get("sessionID")
        consumer = None
        for candidate in self._consumers.values():
            if candidate.session_id == session_id:
                consumer = candidate
                break
        if consumer is None:
            # 观测（2026-10-06 子 Agent 待办断链排查）：todo.updated 到达但无匹配
            # consumer——此前静默 return，排查无迹可循。打出事件 sessionID 与
            # 现有 consumer 的 session_id 集合，实锤匹配断点在哪一侧。
            self._logger.warning(
                "todo_updated_consumer_missed",
                extra={
                    "event_session_id": session_id,
                    "consumer_session_ids": sorted(
                        str(c.session_id) for c in self._consumers.values()
                    ),
                    "payload": str(data)[:200],
                },
            )
            return
        todos = data.get("todos")
        self._logger.info(
            "todo_updated_received",
            extra={
                "task_id": consumer.task_id,
                "session_id": session_id,
                "todo_count": len(todos) if isinstance(todos, list) else 0,
            },
        )
        await consumer.handle_todo_event(data)
