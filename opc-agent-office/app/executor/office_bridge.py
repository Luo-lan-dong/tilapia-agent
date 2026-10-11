"""办公任务桥接层（ADR-062）：中枢派发 ↔ 工具循环执行。

接口与编程节点 TaskBridge 对齐（hub_client 契约）：execute_task / interrupt /
handle_file_fetch / shutdown。任务生命周期：凭据校验 → 工作目录准备 →
工具循环 → 产物清单上报 → 完成回传；静默期心跳看门狗防前端假死（ADR-055）。
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import time
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import AgentConfig
from app.executor.file_scanner import is_excluded_relative_path, scan_file_tree
from app.executor.llm_client import LLMClient, LLMError
from app.executor.tool_loop import ToolLoopRunner, default_registry
from app.executor.verify import VerifyPlan, VerifyReport, run_verify_loop
from app.models.schemas import TaskDispatchPayload

logger = logging.getLogger(__name__)

#: 产物分块大小（与中枢消费端一致，对齐编程节点 task_bridge）
_FILE_CHUNK_SIZE = 512 * 1024
#: 打包产物大小上限（字节）
_MAX_PACK_BYTES = 500 * 1024 * 1024
#: 看门狗轮询间隔（秒，ADR-055）
_WATCHDOG_POLL_INTERVAL_SECONDS = 10.0
#: 验收阻断事件名（ADR-065 M4 复用中枢既有事件，中枢零改动）
_VERIFY_BLOCKED_EVENT = "agent.task_verify_blocked"
#: 成本护栏硬停事件名（ADR-065 M7：复用节点既有事件发送通道，中枢零改动）
_BUDGET_STOP_EVENT = "agent.task_budget_stop"

# --------------------------------------------------------------------------- #
# 轨迹镜像（ADR-065 M5）
# --------------------------------------------------------------------------- #
#: 轨迹行类型白名单（与中枢 ``AGENT_TRACE_KINDS`` 对齐：status/reasoning/text/tool
#: 四类 + M7 新增 ``usage`` 用量行；todo 办公节点不产出——待办已在进度事件里上报）
_TRACE_KINDS: frozenset[str] = frozenset({"status", "reasoning", "text", "tool", "usage"})
_TRACE_KIND_STATUS = "status"
_TRACE_KIND_USAGE = "usage"
#: 轨迹攒批行数：非 status 行攒够该数才发一次 HTTP（status 行始终即时上报）
_TRACE_BATCH_ROWS = 8
#: 轨迹行单个文本字段截断长度（中枢单行 8KB 截断红线之前先自我收敛）
_TRACE_FIELD_MAX_CHARS = 4000

#: 脱敏替换标记（与编程节点 agent_trace.redact_payload 同款）
_REDACTED = "***"
#: 脱敏：``sk-`` 形态的 API 密钥（OpenAI/DeepSeek 等通用前缀）
_TRACE_SK_RE = re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9_-]{6,}")
#: 脱敏：常见凭据赋值（``api_key=xxx`` / ``token: xxx`` / ``password=xxx``，含引号值）
_TRACE_CREDENTIAL_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|secret|"
    r"password|passwd|pwd|authorization|token)\b"
    r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
)
#: 敏感键名（整键打码）；全词匹配避免误伤 ``tokens`` 这类用量字段
_TRACE_SENSITIVE_KEY_RE = re.compile(
    r"(?i)^(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|secret|"
    r"password|passwd|pwd|authorization|credential|credentials)$"
)


def redact_trace_text(text: str) -> str:
    """对单条文本应用轨迹脱敏规则（``sk-`` 串 / 凭据赋值）。"""
    return _TRACE_CREDENTIAL_RE.sub(r"\1\2" + _REDACTED, _TRACE_SK_RE.sub(_REDACTED, text))


def redact_trace_payload(value: Any) -> Any:
    """递归脱敏轨迹 payload：字符串值过规则，敏感键名的值整体打码。

    轨迹行是**落库镜像**（中枢只存不管），payload 会原样进库并可在前端回看，
    因此凭据必须在节点侧、**上报之前**抹掉：``sk-`` 形态密钥串、``api_key=`` /
    ``token:`` / ``password=`` 等赋值文本，以及键名本身就是敏感词的字段值。
    """
    if isinstance(value, str):
        return redact_trace_text(value)
    if isinstance(value, dict):
        return {
            key: (
                _REDACTED
                if isinstance(key, str) and _TRACE_SENSITIVE_KEY_RE.match(key)
                else redact_trace_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_trace_payload(item) for item in value]
    return value


def cap_trace_value(value: Any) -> Any:
    """递归截断轨迹 payload 中的超长字符串（**脱敏之后**应用）。

    中枢单行 ``payload`` 超 8 KB 会被截断并打 ``truncated`` 标记；节点侧先按
    ``_TRACE_FIELD_MAX_CHARS`` 自我收敛，保证整行远小于红线、内容可读。
    """
    if isinstance(value, str):
        if len(value) <= _TRACE_FIELD_MAX_CHARS:
            return value
        return value[:_TRACE_FIELD_MAX_CHARS] + "…【截断】"
    if isinstance(value, dict):
        return {key: cap_trace_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [cap_trace_value(item) for item in value]
    return value


def office_session_id(task_id: str) -> str:
    """office 节点的会话标识（同一任务内**稳定不变**）。

    来源核实（只读中枢契约，M5 要求「与中枢会话标识对齐」）：

    - 中枢派发 payload（``TaskDispatchPayload``）**不含任何会话标识字段**，office
      节点也没有 tilacode 会话（无外部执行进程），不存在可直接对齐的真实会话 ID；
    - 中枢 ``task_event_handler._handle_task_completed`` 明确注释：payload 里的
      ``session_id``（tilacode 会话标识/项目键）**不写入** ``tasks.session_id``
      （该字段外键关联 ``task_building_sessions.id``），中枢只当它是节点侧标注；
    - 故沿用既有合成值 ``office_{task_id[:8]}``，并**统一由本函数产出**：任务完成、
      验收阻断、轨迹行携带的 ``session_id`` 逐字一致，同一任务内稳定不变，
      前端/中枢可按该值把同一任务的上行事件归并到一起。
    """
    return f"office_{task_id[:8]}"


@dataclass
class BridgeCallbacks:
    """中枢上报回调集合（与编程节点 BridgeCallbacks 字段一致）。"""

    on_progress: Callable[..., Awaitable[None]]
    on_task_started: Callable[[str, str], Awaitable[None]]
    on_completed: Callable[..., Awaitable[None]]
    on_failed: Callable[[str, str], Awaitable[None]]
    on_file_tree: Callable[[str, list[dict[str, Any]], bool], Awaitable[None]]
    on_file_chunk: Callable[..., Awaitable[None]]
    on_file_error: Callable[[str, str], Awaitable[None]]
    #: 验收阻断上报（ADR-065 M4）：``(task_id, session_id, report) -> Awaitable``，
    #: 签名与编程节点 HubClient.report_verify_blocked 一致；缺省时按调用方
    #: 既有通道自动解析（见 ``_resolve_verify_blocked_reporter``）。
    on_verify_blocked: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None = None
    #: 节点 Agent 轨迹上报（ADR-065 M5）：``(task_id, *, node_id, session_id, rows)``，
    #: 由 HubClient 装配时通过 ``bind_agent_trace`` 注入（HTTP 旁路）；缺省 None 表示
    #: 不上报（轨迹是旁路，缺失不影响任务）。
    on_agent_trace: Callable[..., Awaitable[None]] | None = None
    #: 成本护栏硬停上报（ADR-065 M7）：``(task_id, session_id, reason, usage)``；
    #: 缺省 None 时按调用方既有通道自动解析（见 ``_resolve_budget_stop_reporter``）。
    on_budget_stop: Callable[[str, str, str, dict[str, Any]], Awaitable[None]] | None = None


def _sanitize_fetch_path(path: str | None) -> str:
    """清洗产物拉取路径：拒绝空串、绝对路径与 ``..`` 越界片段。"""
    if not path:
        raise FileNotFoundError("路径为空")
    normalized = path.strip().lstrip("/")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise FileNotFoundError(f"非法路径: {path}")
    return "/".join(parts)


class OfficeBridge:
    """办公任务执行桥接：接单 → 工具循环 → 交付。"""

    def __init__(self, config: AgentConfig, callbacks: BridgeCallbacks) -> None:
        self._config = config
        self._callbacks = callbacks
        self._tasks: dict[str, TaskRunState] = {}
        # 任务 → 工作目录持久映射（ADR-028 产物按需拉取）：任务完成后中枢仍会
        # 按 file_tree 触发增量拉取，映射随任务完成清除会导致「工作目录不存在」
        # （2026-09-28 实测）；进程重启后由 JSON 登记簿恢复（对齐编程节点 TaskMappingStore）
        self._task_workdirs: dict[str, str] = {}
        self._workdir_store_path = Path("data/task_workdirs.json")
        self._logger = logging.getLogger(__name__)
        # 轨迹镜像（ADR-065 M5）：task_id → 已分配的最大 seq（按任务从 1 单调递增，
        # 任务结束清空）；task_id → 待发轨迹行（攒批上报，任务结束清空）
        self._trace_seq: dict[str, int] = {}
        self._trace_pending: dict[str, list[dict[str, Any]]] = {}
        self._load_workdir_store()

    def bind_agent_trace(self, reporter: Callable[..., Awaitable[None]]) -> None:
        """绑定轨迹上报通道（HubClient 装配时注入，ADR-065 M5）。

        上报走 HTTP 旁路（``HubClient.report_agent_trace``）；未绑定时
        ``on_agent_trace`` 保持缺省 None，``_report_trace`` 静默跳过——轨迹缺失
        绝不影响任务执行与完成判定。
        """
        self._callbacks.on_agent_trace = reporter

    def _load_workdir_store(self) -> None:
        """启动时恢复工作目录登记簿（文件不存在/损坏时静默忽略）。"""
        try:
            import json

            raw = json.loads(self._workdir_store_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._task_workdirs.update({str(k): str(v) for k, v in raw.items()})
        except (OSError, ValueError):
            pass

    def _remember_workdir(self, task_id: str, work_dir: Path) -> None:
        """登记任务工作目录并落盘（幂等覆盖）。"""
        import json

        self._task_workdirs[task_id] = str(work_dir)
        try:
            self._workdir_store_path.parent.mkdir(parents=True, exist_ok=True)
            self._workdir_store_path.write_text(
                json.dumps(self._task_workdirs, ensure_ascii=False, indent=0),
                encoding="utf-8",
            )
        except OSError as exc:
            self._logger.warning(
                "workdir_store_write_failed", extra={"task_id": task_id, "error": str(exc)}
            )

    def _lookup_workdir(self, task_id: str) -> Path | None:
        """反查任务工作目录：在跑任务优先，其次持久登记簿。"""
        state = self._tasks.get(task_id)
        if state is not None and state.work_dir is not None:
            return state.work_dir
        recorded = self._task_workdirs.get(task_id)
        return Path(recorded) if recorded else None

    async def execute_task(self, dispatch: TaskDispatchPayload) -> None:
        """执行办公任务（hub_client 已发 accepted）。

        轨迹镜像（ADR-065 M5）：任务开始与结束各发一条 ``status`` 行；任务结束时
        另发一条 ``usage`` 行（ADR-065 M7 用量可见：prompt/completion/rounds/
        budget_percent 累计值）。结束行与剩余待发轨迹在 finally 里排水，随后清空
        该任务的 seq 计数与待发队列（下一个任务重新从 1 开始，中枢按
        ``(task_id, seq)`` 幂等）。
        """
        task_id = dispatch.task_id
        state = TaskRunState(task_id=task_id)
        self._tasks[task_id] = state
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(state),
        )
        # 起始 status 行（stage 复用既有枚举：TaskRunState 初始值 preparing）
        await self._report_trace_status(state, state.last_stage, "任务开始执行")
        try:
            await self._run(dispatch, state)
        finally:
            state.cancel.set()
            heartbeat_task.cancel()
            self._tasks.pop(task_id, None)
            try:
                # 用量行（ADR-065 M7）+ 结束 status 行 + 剩余轨迹排水（顺序固定：
                # usage 在结束行之前，保证「任务执行结束」始终是最后一条 status 行）；
                # 轨迹是旁路，取消/失败路径下上报异常也不改变任务结局（只 warning）
                await self._report_usage(state)
                await self._report_trace_status(state, state.last_stage, "任务执行结束")
                await self._flush_trace(task_id)
            finally:
                self._trace_seq.pop(task_id, None)
                self._trace_pending.pop(task_id, None)

    async def _run(self, dispatch: TaskDispatchPayload, state: TaskRunState) -> None:
        """任务主体。"""
        task_id = dispatch.task_id
        executor_model = dispatch.executor_model or {}
        model = str(executor_model.get("model") or "")
        api_key = str(executor_model.get("api_key") or "")
        base_url = str(executor_model.get("base_url") or "")
        if not (model and api_key and base_url):
            await self._callbacks.on_failed(
                task_id, "missing_executor_credentials: 中枢未下发执行模型凭据"
            )
            return
        await self._callbacks.on_task_started(task_id, model)
        await self._report_progress(state, 5, "preparing", "正在准备任务工作目录")

        work_dir = self._resolve_work_dir(dispatch)
        state.work_dir = work_dir
        self._remember_workdir(task_id, work_dir)
        llm = LLMClient(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=self._config.office_llm_timeout_seconds,
            max_retries=self._config.office_llm_max_retries,
        )
        registry = default_registry(work_dir, self._config)
        runner = ToolLoopRunner(
            self._config,
            llm,
            registry,
            on_progress=lambda progress, stage, message: self._report_progress(
                state, progress, stage, message
            ),
            on_sources=lambda entries: self._append_sources(state, entries),
            # 轨迹镜像（ADR-065 M5）：每轮 LLM 的 reasoning/text 与每次工具调用
            # 由工具循环回调进来，seq/脱敏/攒批统一在 _report_trace 内处理
            on_trace=lambda kind, payload: self._report_trace(
                kind, payload, task_id=task_id
            ),
            # 用量累计（ADR-065 M7）：每次执行（首轮 + 各回灌轮）结束回调用量，
            # 任务结束时由 execute_task 的 finally 发一条 usage 轨迹行
            on_usage=lambda payload: self._accumulate_usage(state, payload),
        )
        instruction = self._build_instruction(dispatch)
        # 交付物时间下界（ADR-065 M4）：只把本轮写入的文件当作交付物校验，
        # 避免同一 output_dir 下历史任务的旧产物参与验收
        started_at = time.time()
        try:
            outcome = await runner.run(task_id, instruction, state.cancel)
        except LLMError as exc:
            await self._callbacks.on_failed(task_id, f"llm_unavailable: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 — 兜底转失败上报（ADR-055 P0-3）
            self._logger.exception("office_task_crashed", extra={"task_id": task_id})
            await self._callbacks.on_failed(task_id, f"agent_internal_error: {exc}")
            return
        if state.cancel.is_set():
            await self._callbacks.on_failed(task_id, "interrupted")
            return

        # 成本护栏硬停（ADR-065 M7 / Spec D5）：token 预算耗尽即停止继续调用 LLM，
        # **不得假装成功**——不再跑交付质量闸、不回 completed，而是上报明确失败原因
        # （预算用量一并带上），由中枢按既有失败语义呈现
        if outcome.stopped_reason == "token_budget":
            await self._report_budget_hard_stop(task_id, state)
            await self._callbacks.on_failed(task_id, self._budget_failure_reason(state))
            return

        # 交付质量闸（ADR-065 M4）：判据只来自中枢下发的规格/checklist；不过 →
        # 只回灌未通过条目重做（≤5 轮）；5 轮仍不过 → verify_blocked 阻断
        try:
            report = await self._run_verify_gate(
                dispatch, state, work_dir, llm, runner, started_at
            )
        except LLMError as exc:
            # 回灌轮 LLM 不可用：沿用既有失败语义（llm_unavailable）
            await self._callbacks.on_failed(task_id, f"llm_unavailable: {exc}")
            return
        if state.cancel.is_set():
            await self._callbacks.on_failed(task_id, "interrupted")
            return
        if not report.passed:
            await self._report_verify_blocked(task_id, report)
            return

        summary = outcome.summary or "任务完成。"
        if outcome.warnings:
            summary += "\n\n注意：" + "；".join(outcome.warnings)
        if report.source != "none":
            summary += f"\n\n交付质量闸：{report.summary()}"
        state.mark_active()
        # 终态 todos：全部置 completed 并随最后一次进度上报——否则最后一项
        # 永远停留在「进行中」，前端看起来像卡死在最后一步（2026-09-28 实测）
        state.complete_all_todos()
        await self._report_progress(state, 100, "summarizing", "任务完成，正在同步交付物清单")
        await self._report_file_tree(task_id, work_dir)
        await self._callbacks.on_completed(
            task_id,
            office_session_id(task_id),
            change_summary=summary[:2000],
        )
        self._logger.info(
            "office_task_completed",
            extra={
                "task_id": task_id,
                "rounds": outcome.rounds,
                "tokens": outcome.prompt_tokens + outcome.completion_tokens,
                "stopped_reason": outcome.stopped_reason,
                "work_dir": str(work_dir),
            },
        )

    def _resolve_work_dir(self, dispatch: TaskDispatchPayload) -> Path:
        """决定任务工作目录：output_dir 优先（ADR-028），否则按任务 ID 分配。"""
        output_dir = dispatch.output_dir
        if output_dir:
            candidate = Path(output_dir)
            if candidate.is_absolute():
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
        work_dir = Path(self._config.workspace_root) / dispatch.task_id[:8]
        work_dir.mkdir(parents=True, exist_ok=True)
        return work_dir

    def _build_instruction(self, dispatch: TaskDispatchPayload) -> str:
        """组装任务指令：任务名 + 描述 + 规格三件套 + 修改说明。"""
        parts: list[str] = []
        if dispatch.task_name:
            parts.append(f"任务名称：{dispatch.task_name}")
        if dispatch.description:
            parts.append(f"任务要求：\n{dispatch.description}")
        spec = dispatch.specification
        if spec is not None:
            if spec.spec:
                parts.append(f"规格说明（spec）：\n{spec.spec[:6000]}")
            if spec.tasks:
                parts.append(f"任务拆解（tasks）：\n{spec.tasks[:4000]}")
            if spec.checklist:
                parts.append(f"验收清单（checklist）：\n{spec.checklist[:4000]}")
        if dispatch.change_brief:
            parts.append(f"本轮补充说明：\n{dispatch.change_brief[:2000]}")
        return "\n\n".join(parts) or "请根据对话上下文完成办公任务。"

    # ----------------------------------------------------------------- #
    # 成本护栏与用量可见（ADR-065 M7）
    # ----------------------------------------------------------------- #

    def _accumulate_usage(self, state: "TaskRunState", payload: dict[str, Any]) -> None:
        """累计任务用量（token 相加、轮次取各次执行的最大值）。

        每次执行（首轮 + 各回灌轮）结束都会回调一次；``rounds`` 取最大值而非求和
        ——它是「单次执行用了几轮」的上限语义，累加会与「轮次上限」对不上。
        """
        state.prompt_tokens += int(payload.get("prompt_tokens") or 0)
        state.completion_tokens += int(payload.get("completion_tokens") or 0)
        state.rounds = max(state.rounds, int(payload.get("rounds") or 0))

    def _budget_percent(self, state: "TaskRunState") -> int:
        """任务累计用量占 token 预算的百分比（夹取 0..100）。"""
        budget = self._config.office_llm_token_budget
        if budget <= 0:
            return 0
        used = state.prompt_tokens + state.completion_tokens
        return max(0, min(100, round(used * 100 / budget)))

    def _budget_failure_reason(self, state: "TaskRunState") -> str:
        """硬停失败原因（Spec D5：``reason=budget_hard_exceeded`` + **累计**用量）。

        用量取任务累计值（首轮 + 各回灌轮），与 ``usage`` 轨迹行、硬停事件同一口径
        ——三者对不上会让排查时无法判断「到底烧了多少预算」。
        """
        return (
            "budget_hard_exceeded: token 预算耗尽，已停止继续调用 LLM"
            f"（已用 {state.prompt_tokens + state.completion_tokens}/"
            f"{self._config.office_llm_token_budget} tokens，{state.rounds} 轮）"
        )

    def _resolve_budget_stop_reporter(
        self,
    ) -> Callable[[str, str, str, dict[str, Any]], Awaitable[None]] | None:
        """解析成本硬停上报通道（复用既有事件链路，对齐编程节点能力）。

        优先级：回调宿主的专用方法 ``report_budget_stop``（编程节点
        ``HubClient`` 已有，office HubClient 尚未提供）→ 回调宿主的通用事件发送
        ``_send_event``（**office 现行生效路径**）→ 显式回调
        ``on_budget_stop``。三种通道在本模块内统一为
        ``(task_id, session_id, reason, usage)`` 四参调用。

        注：本方法只做「能力探测」，**不新增中枢事件类型**——office 当前走
        ``_send_event`` 上报，中枢按既有事件语义处理。
        """
        callback = self._callbacks.on_budget_stop
        if callback is not None:
            return callback
        owner = getattr(self._callbacks.on_failed, "__self__", None)
        if owner is None:
            return None
        dedicated = getattr(owner, "report_budget_stop", None)
        if callable(dedicated):

            async def _send_via_dedicated(
                task_id: str, session_id: str, reason: str, usage: dict[str, Any]
            ) -> None:
                """调用宿主专用方法（关键字入参，字段与编程节点对齐）。"""
                await dedicated(task_id, session_id=session_id, reason=reason, usage=usage)

            return _send_via_dedicated
        sender = getattr(owner, "_send_event", None)
        if not callable(sender):
            return None

        async def _send_via_event(
            task_id: str, session_id: str, reason: str, usage: dict[str, Any]
        ) -> None:
            """按既有线上字段发送预算硬停事件。"""
            await sender(
                _BUDGET_STOP_EVENT,
                {
                    "task_id": task_id,
                    "session_id": session_id,
                    "reason": reason,
                    "usage": usage,
                },
            )

        return _send_via_event

    async def _report_budget_hard_stop(self, task_id: str, state: "TaskRunState") -> None:
        """上报成本护栏硬停（ADR-065 M7）：先发 ``usage`` 轨迹行，再发事件。

        轨迹行（``kind=usage``）保证前端折叠块能看到「用满预算而停」；事件走既有
        通道让中枢看到明确 ``reason``（**不假装成功**）。两条都是旁路：失败只
        warning，不改变任务结局（结局由调用方的 ``on_failed`` 决定）。
        """
        self._logger.warning(
            "office_budget_hard_stop",
            extra={
                "event": "office_budget_hard_stop",
                "task_id": task_id,
                "budget_percent": self._budget_percent(state),
                "tokens": state.prompt_tokens + state.completion_tokens,
                "rounds": state.rounds,
            },
        )
        await self._report_usage(state)
        reason = self._budget_failure_reason(state)
        reporter = self._resolve_budget_stop_reporter()
        if reporter is None:
            self._logger.warning(
                "budget_stop_unreported",
                extra={"event": "budget_stop_unreported", "task_id": task_id, "reason": reason},
            )
            return
        try:
            await reporter(
                task_id,
                office_session_id(task_id),
                reason,
                self._usage_payload(state),
            )
        except Exception as exc:  # noqa: BLE001 — 旁路：上报失败不改变任务结局，仅告警
            self._logger.warning(
                "budget_stop_report_failed",
                extra={
                    "event": "budget_stop_report_failed",
                    "task_id": task_id,
                    "error": str(exc),
                },
            )

    def _usage_payload(self, state: "TaskRunState") -> dict[str, Any]:
        """任务累计用量 payload（usage 轨迹行与硬停上报共用同一口径）。"""
        return {
            "prompt_tokens": state.prompt_tokens,
            "completion_tokens": state.completion_tokens,
            "rounds": state.rounds,
            "budget_percent": self._budget_percent(state),
        }

    async def _report_usage(self, state: "TaskRunState") -> None:
        """任务结束时发一条 ``usage`` 轨迹行（ADR-065 M7 用量可见）。

        无用量（未产生任何 token，如凭据缺失/启动即失败）时不发空行；上报失败只
        warning（``_report_trace`` 内已兜底），绝不影响任务结局。
        """
        if state.prompt_tokens <= 0 and state.completion_tokens <= 0:
            return
        await self._report_trace(
            _TRACE_KIND_USAGE, self._usage_payload(state), task_id=state.task_id
        )

    async def _run_verify_gate(
        self,
        dispatch: TaskDispatchPayload,
        state: "TaskRunState",
        work_dir: Path,
        llm: LLMClient,
        runner: ToolLoopRunner,
        started_at: float,
    ) -> VerifyReport:
        """跑三层交付质量闸（ADR-065 M4）：判据只来自中枢下发的规格/checklist。

        不过 → 把**未通过条目 + 期望 + 实际**作为新一轮输入回灌重做（≤5 轮，
        已达标项不得重写）；5 轮仍不过 → 由调用方上报 ``agent.task_verify_blocked``。
        通过 → 交付路径与既有行为完全一致。
        """
        plan = VerifyPlan.from_specification(dispatch.specification)

        async def repair(instruction: str, round_index: int) -> None:
            """回灌回调：把只含未通过条目的修复指令送进工具循环。

            每次执行结束回调用量（ADR-065 M7），异常路径（LLM 不可用上抛）也不丢
            ——用量是任务真实成本，硬停判定与 usage 轨迹行都要它。
            """
            outcome = await runner.run(
                dispatch.task_id, instruction, state.cancel, repair_round=round_index
            )
            self._accumulate_usage(
                state,
                {
                    "prompt_tokens": outcome.prompt_tokens,
                    "completion_tokens": outcome.completion_tokens,
                    "rounds": outcome.rounds,
                },
            )

        report = await run_verify_loop(
            work_dir,
            plan,
            llm=llm,
            repair=repair,
            on_round=lambda item, phase: self._on_verify_round(state, item, phase),
            since=started_at,
        )
        self._logger.info(
            "office_task_verify_result",
            extra={
                "task_id": dispatch.task_id,
                "passed": report.passed,
                "layer": report.layer,
                "source": report.source,
                "items": len(report.items),
                "failures": len(report.failures),
                "repair_rounds": report.repair_round,
            },
        )
        return report

    async def _on_verify_round(
        self, state: "TaskRunState", report: VerifyReport, phase: str
    ) -> None:
        """验收轮次进度上报（复用既有 testing 阶段枚举，不新增阶段）。"""
        if phase == "initial":
            message = "正在核对验收清单（L0 结构 / L1 内容 / L2 语义）"
        elif report.passed:
            message = f"交付质量闸已通过（{phase}）"
        else:
            first = report.failures[0] if report.failures else None
            detail = f"{first.layer}/{first.id} {first.requirement[:40]}" if first else "未通过"
            message = f"交付质量闸未通过（{phase}）：{detail}"
        await self._report_progress(state, 92, "testing", message)

    async def _report_verify_blocked(self, task_id: str, report: VerifyReport) -> None:
        """上报 ``agent.task_verify_blocked``（ADR-065 M4；中枢零改动）。

        payload 与中枢 ``_handle_task_verify_blocked`` / ``TaskVerifyBlockedPayload``
        对齐：``task_id`` / ``session_id`` / ``reason``（未通过摘要，含「卡在哪一条」）/
        ``report``（``status`` / ``source`` / ``must_pass_failures`` / ``repair_rounds`` /
        ``summary`` / ``checks``）；中枢据此置 ``verify_blocked`` 并在任务对话区找用户决定。

        ``session_id`` 由 :func:`office_session_id` 统一产出（与 ``agent.task_completed``
        及轨迹行携带的值逐字一致）；上报通道自 ADR-065 M5 起优先走
        ``HubClient.report_verify_blocked``（该事件同时纳入断线缓冲队列）。
        """
        session_id = office_session_id(task_id)
        report_payload = report.to_blocked_report()
        self._logger.warning(
            "office_task_verify_blocked",
            extra={
                "task_id": task_id,
                "layer": report.layer,
                "source": report.source,
                "repair_rounds": report.repair_round,
                "must_pass_failures": len(report.failures),
            },
        )
        reporter = self._resolve_verify_blocked_reporter()
        if reporter is None:
            self._logger.warning(
                "task_verify_blocked_unreported",
                extra={"task_id": task_id, "report": report_payload},
            )
            return
        try:
            await reporter(task_id, session_id, report_payload)
        except Exception as exc:  # noqa: BLE001 — 上报失败不改变阻断态判定，仅告警
            self._logger.warning(
                "task_verify_blocked_report_failed",
                extra={"task_id": task_id, "error": str(exc)},
            )

    def _resolve_verify_blocked_reporter(
        self,
    ) -> Callable[[str, str, dict[str, Any]], Awaitable[None]] | None:
        """解析验收阻断上报通道（沿用中枢既有上报链路，签名与编程节点一致）。

        优先级：显式回调 ``on_verify_blocked`` → 回调宿主的专用方法
        ``report_verify_blocked``（ADR-065 M5 起 office HubClient 已具备，**现行生效
        路径**）→ 回调宿主的通用事件发送 ``_send_event``（旧宿主兜底）。

        三种通道在本模块内统一为 ``(task_id, session_id, report)`` 三参调用，
        由各自的包装函数适配宿主真实签名。
        """
        callback = self._callbacks.on_verify_blocked
        if callback is not None:
            return callback
        owner = getattr(self._callbacks.on_failed, "__self__", None)
        if owner is None:
            return None
        dedicated = getattr(owner, "report_verify_blocked", None)
        if callable(dedicated):

            async def _send_via_dedicated(
                task_id: str, session_id: str, payload: dict[str, Any]
            ) -> None:
                """调用宿主专用方法（关键字入参，字段与编程节点逐字对齐）。"""
                await dedicated(
                    task_id,
                    session_id=session_id,
                    reason=str(payload.get("summary") or "")[:2000],
                    report=payload,
                )

            return _send_via_dedicated
        sender = getattr(owner, "_send_event", None)
        if not callable(sender):
            return None

        async def _send_via_event(
            task_id: str, session_id: str, payload: dict[str, Any]
        ) -> None:
            """按编程节点 report_verify_blocked 的线上字段发送阻断事件。"""
            await sender(
                _VERIFY_BLOCKED_EVENT,
                {
                    "task_id": task_id,
                    "session_id": session_id,
                    "reason": str(payload.get("summary") or "")[:2000],
                    "report": payload,
                },
            )

        return _send_via_event

    async def _report_progress(self, state: "TaskRunState", progress: int, stage: str, message: str) -> None:
        """进度上报（刷活动时间并记录最近态；stage 变化或 todos 脏位时带上待办）。

        轨迹镜像（ADR-065 M5）：**阶段变更点**（``apply_stage`` 返回 True）额外
        发一条 ``status`` 轨迹行，同阶段重复上报不重复发；stage 取值一律沿用既有
        枚举（preparing/planning/searching/coding/testing/summarizing），不扩展。
        """
        state.mark_active()
        state.progress = max(0, min(100, progress))
        state.last_stage = stage
        state.last_message = message
        stage_changed = state.apply_stage(stage)
        if stage_changed:
            await self._report_trace_status(state, stage, message)
        kwargs: dict[str, Any] = {}
        if stage_changed or state.todos_dirty:
            kwargs["todos"] = state.todos_snapshot()
            state.todos_dirty = False
        await self._callbacks.on_progress(
            state.task_id,
            state.progress,
            stage=stage,
            message=message[:120],
            **kwargs,
        )

    async def _report_trace_status(self, state: "TaskRunState", stage: str, message: str) -> None:
        """上报一条 ``status`` 轨迹行并即时排水（ADR-065 M5）。

        status 行承载「当前在哪一阶段」，前端折叠块要求即时可见，故不入攒批队列
        而立即发送；payload 固定为 ``stage`` / ``message`` / ``progress``。
        """
        await self._report_trace(
            _TRACE_KIND_STATUS,
            {
                "stage": stage,
                "message": message[:200],
                "progress": state.progress,
            },
            task_id=state.task_id,
            immediate=True,
        )

    async def _report_trace(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        task_id: str,
        immediate: bool = False,
    ) -> None:
        """采集一条轨迹行（ADR-065 M5）：分配 seq、脱敏、入队；按需即时上报。

        - **seq**：按 task 从 1 单调递增（中枢以 ``(task_id, seq)`` 幂等去重），
          任务结束在 ``execute_task`` 的 finally 里清空计数；
        - **脱敏**：payload 内 ``sk-`` 形态串与 ``api_key``/``token``/``password``
          等敏感键值一律替换为 ``***``（落库前完成，见 ``redact_trace_payload``）；
        - **攒批**：非 status 行先入队，攒够 ``_TRACE_BATCH_ROWS`` 行才发一次 HTTP；
        - **旁路**：未绑定上报通道或上报失败都只记 warning，绝不影响任务执行、
          完成判定与进度上报（所有调用点都在 ``_flush_trace`` 内兜底）。
        """
        if self._callbacks.on_agent_trace is None:
            return
        if kind not in _TRACE_KINDS:
            # 白名单外的 kind 中枢会整行计入 ignored（白占单任务行数配额），此处直接不发
            self._logger.debug(
                "agent_trace_kind_skipped",
                extra={"event": "agent_trace_kind_skipped", "task_id": task_id, "kind": kind},
            )
            return
        try:
            seq = self._trace_seq.get(task_id, 0) + 1
            self._trace_seq[task_id] = seq
            self._trace_pending.setdefault(task_id, []).append(
                {
                    "seq": seq,
                    "kind": kind,
                    "payload": cap_trace_value(redact_trace_payload(payload)),
                }
            )
            if immediate or len(self._trace_pending[task_id]) >= _TRACE_BATCH_ROWS:
                await self._flush_trace(task_id)
        except Exception as exc:  # noqa: BLE001 — 旁路：采集/入队失败绝不影响主链路
            self._logger.warning(
                "agent_trace_collect_failed",
                extra={
                    "event": "agent_trace_collect_failed",
                    "task_id": task_id,
                    "kind": kind,
                    "error": str(exc),
                },
            )

    async def _flush_trace(self, task_id: str) -> None:
        """把待发轨迹行整批上报中枢（ADR-065 M5）。

        **失败只记 warning**：本方法向调用方抛出异常的只有 ``CancelledError``
        （任务被取消时正常传播），其余异常一律吞掉并告警；失败的行不回队——
        办公节点 M5 只做即时镜像，不做离线持久补报（中枢按 seq 幂等，未来重发安全）。
        """
        reporter = self._callbacks.on_agent_trace
        pending = self._trace_pending.get(task_id)
        if reporter is None or not pending:
            return
        rows = list(pending)
        pending.clear()
        try:
            await reporter(
                task_id,
                node_id=None,
                session_id=office_session_id(task_id),
                rows=rows,
            )
        except Exception as exc:  # noqa: BLE001 — 旁路上报，失败绝不影响任务
            self._logger.warning(
                "agent_trace_report_failed",
                extra={
                    "event": "agent_trace_report_failed",
                    "task_id": task_id,
                    "row_count": len(rows),
                    "error": str(exc),
                },
            )

    async def _report_file_tree(self, task_id: str, work_dir: Path) -> None:
        """扫描工作目录并上报产物清单（ADR-028）。"""
        files, truncated = await asyncio.to_thread(scan_file_tree, work_dir)
        await self._callbacks.on_file_tree(task_id, files, truncated)

    async def _append_sources(self, state: "TaskRunState", entries: list[str]) -> None:
        """联网来源进入待办（脏位标记，随下次进度一并上报）。"""
        state.append_source_todos(entries)
        if state.todos_dirty:
            await self._report_progress(state, state.progress, state.last_stage, state.last_message)

    async def _heartbeat_loop(self, state: "TaskRunState") -> None:
        """静默期心跳（ADR-055）：超过阈值无活动时补发进度事件。"""
        interval = self._config.agent_progress_heartbeat_seconds
        try:
            while not state.cancel.is_set():
                await asyncio.sleep(interval)
                idle = time.monotonic() - state.last_active
                if idle >= interval:
                    await self._callbacks.on_progress(
                        state.task_id,
                        state.progress,
                        stage=state.last_stage,
                        message=f"仍在执行：{state.last_message}（{int(idle)}s 无新事件）",
                    )
        except asyncio.CancelledError:
            return

    async def interrupt(self, task_id: str) -> None:
        """中断执行中任务（中枢取消指令）。"""
        state = self._tasks.get(task_id)
        if state is not None:
            state.cancel.set()

    async def shutdown(self) -> None:
        """优雅关闭：取消全部在跑任务。"""
        for state in self._tasks.values():
            state.cancel.set()

    async def handle_file_fetch(
        self, request_id: str, task_id: str, mode: str, path: str | None
    ) -> None:
        """处理中枢产物拉取（ADR-028）：file 单文件 / zip 整目录。"""
        work_dir = self._lookup_workdir(task_id)
        try:
            if work_dir is None or not work_dir.exists():
                raise FileNotFoundError(f"任务工作目录不存在: {task_id}")
            if mode == "zip":
                payload = await asyncio.to_thread(self._pack_workspace, work_dir)
            else:
                relative = _sanitize_fetch_path(path)
                target = work_dir / relative
                if not target.resolve().is_relative_to(work_dir.resolve()) or not target.is_file():
                    raise FileNotFoundError(f"文件不存在: {path}")
                payload = await asyncio.to_thread(target.read_bytes)
        except Exception as exc:  # noqa: BLE001 — 失败回传 file_error
            await self._callbacks.on_file_error(request_id, str(exc))
            return
        total = max(1, -(-len(payload) // _FILE_CHUNK_SIZE))
        for seq in range(total):
            chunk = payload[seq * _FILE_CHUNK_SIZE : (seq + 1) * _FILE_CHUNK_SIZE]
            await self._callbacks.on_file_chunk(
                request_id, seq, total, base64.b64encode(chunk).decode("ascii"), seq == total - 1
            )

    @staticmethod
    def _pack_workspace(work_dir: Path) -> bytes:
        """打包工作目录（复用 file_scanner 排除规则）。"""
        files, _truncated = scan_file_tree(work_dir)
        buffer = io.BytesIO()
        total = 0
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in files:
                if is_excluded_relative_path(item["path"]):
                    continue
                target = work_dir / item["path"]
                total += item["size"]
                if total > _MAX_PACK_BYTES:
                    raise ValueError("产物打包超出 500MB 上限")
                archive.write(target, item["path"])
        return buffer.getvalue()


class TaskRunState:
    """单个在跑任务的运行态（取消事件 + 活动时间 + 最近进度 + 阶段待办）。"""

    #: 阶段 → 待办文案（searching 为可选阶段，出现时动态插入）
    _STAGE_TODO_CONTENT: dict[str, str] = {
        "planning": "规划大纲与要点",
        "searching": "检索与提炼资料",
        "coding": "编写交付文档",
        "testing": "核对验收清单",
        "summarizing": "整理来源与总结",
    }
    _BASE_ORDER: tuple[str, ...] = ("planning", "coding", "testing", "summarizing")

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.cancel = asyncio.Event()
        self.last_active = time.monotonic()
        self.progress = 0
        self.last_stage = "preparing"
        self.last_message = "准备中"
        self.work_dir: Path | None = None
        # 任务累计用量（ADR-065 M7）：每次执行（首轮 + 各回灌轮）结束累加，
        # 任务结束时发 usage 轨迹行、硬停时随失败原因一并上报
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.rounds = 0
        # 阶段化待办（ADR-055 todos）：前端「待办明细」展示当前在做什么/要做什么
        self.todos: list[dict[str, str]] = [
            {"content": self._STAGE_TODO_CONTENT[stage], "status": "pending"}
            for stage in self._BASE_ORDER
        ]
        self._reported_stage: str | None = None
        self.todos_dirty = False

    def mark_active(self) -> None:
        """刷新活动时间（看门狗依据）。"""
        self.last_active = time.monotonic()

    def apply_stage(self, stage: str) -> bool:
        """同步阶段到待办状态；返回 todos 是否变化（需重新上报）。"""
        changed = stage != self._reported_stage
        if changed:
            self.todos_dirty = True
        if stage == "searching" and all(
            todo["content"] != self._STAGE_TODO_CONTENT["searching"] for todo in self.todos
        ):
            insert_at = next(
                (
                    index
                    for index, todo in enumerate(self.todos)
                    if todo["content"] == self._STAGE_TODO_CONTENT["coding"]
                ),
                len(self.todos),
            )
            self.todos.insert(
                insert_at, {"content": self._STAGE_TODO_CONTENT["searching"], "status": "pending"}
            )
            changed = True
        target_content = self._STAGE_TODO_CONTENT.get(stage)
        target_index = next(
            (index for index, todo in enumerate(self.todos) if todo["content"] == target_content),
            None,
        )
        if target_index is not None:
            for index in range(target_index):
                self.todos[index]["status"] = "completed"
            self.todos[target_index]["status"] = "in_progress"
        self._reported_stage = stage
        return changed

    def complete_all_todos(self) -> None:
        """终态：全部待办置 completed（任务完成前最后一次上报用）。"""
        for todo in self.todos:
            todo["status"] = "completed"
        self.todos_dirty = True

    def append_source_todos(self, entries: list[str]) -> None:
        """追加联网参考来源条目（任务摘要待办下方展示访问了哪些站点）。"""
        added = False
        for entry in entries:
            content = f"参考来源：{entry}"
            if any(todo["content"] == content for todo in self.todos):
                continue
            source_count = sum(1 for todo in self.todos if todo["content"].startswith("参考来源："))
            if source_count >= 12:  # 防止来源条目无限增长挤占待办区
                continue
            self.todos.append({"content": content, "status": "completed"})
            added = True
        if added:
            self.todos_dirty = True

    def todos_snapshot(self) -> list[dict[str, str]]:
        """待办快照（防外部修改内部列表）。"""
        return [dict(todo) for todo in self.todos]
