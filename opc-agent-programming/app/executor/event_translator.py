"""tilacode SSE 事件翻译器。

把 tilacode session 事件流（``session.next.*`` 系列及 ``todo.updated``）
翻译为中枢进度上报 ``ProgressUpdate``；权限事件、未知事件返回 None，由
任务桥接层兜底处理。
"""

from dataclasses import dataclass
from typing import Any

# 事件类型常量
_EVENT_STEP_STARTED: str = "session.next.step.started"
_EVENT_STEP_ENDED: str = "session.next.step.ended"
_EVENT_TOOL_CALLED: str = "session.next.tool.called"
_EVENT_TOOL_SUCCESS: str = "session.next.tool.success"
_EVENT_TOOL_FAILED: str = "session.next.tool.failed"
_EVENT_TEXT_ENDED: str = "session.next.text.ended"
_EVENT_TODO_UPDATED: str = "todo.updated"
_EVENT_PERMISSION_ASKED: str = "permission.v2.asked"
# ADR-055 P1-8 补充：真实 schema（packages/schema/src/session-event.ts）中
# 存在但此前未处理的 durable 事件
_EVENT_TOOL_PROGRESS: str = "session.next.tool.progress"
_EVENT_RETRIED: str = "session.next.retried"
_EVENT_COMPACTION_STARTED: str = "session.next.compaction.started"
_EVENT_COMPACTION_ENDED: str = "session.next.compaction.ended"
# ADR-063 M2：会话轨迹分类所需的思考/正文增量事件
_EVENT_REASONING_DELTA: str = "session.next.reasoning.delta"
_EVENT_REASONING_ENDED: str = "session.next.reasoning.ended"
_EVENT_TEXT_DELTA: str = "session.next.text.delta"

# 进度估算常量：初始 10、阶段开始 20、小步 +2 封顶 90、完成 100
_PROGRESS_READY: int = 10
_PROGRESS_STEP_STARTED: int = 20
_PROGRESS_ADVANCE: int = 2
_PROGRESS_CAP: int = 90
_PROGRESS_COMPLETE: int = 100
_TEXT_SUMMARY_MAX: int = 120

# todo 状态
_TODO_STATUS_COMPLETED: str = "completed"
_TODO_STATUS_IN_PROGRESS: str = "in_progress"

#: 工具输入中可作为「动作对象」展示的字段优先级（相对路径/模式/命令）
_TARGET_KEYS: tuple[str, ...] = (
    "filePath",
    "file_path",
    "path",
    "pattern",
    "query",
    "command",
    "cmd",
)


def _primary_target(tool_input: Any) -> str:
    """从工具输入中提取可展示的动作对象（优先相对路径，其次命令/模式）。

    Args:
        tool_input: ``session.next.tool.called`` 的 ``input`` 字段。

    Returns:
        截断到 ``_COMMAND_SUMMARY_MAX`` 的展示文本；无法提取时为空串。
    """
    if not isinstance(tool_input, dict):
        return ""
    for key in _TARGET_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip().replace("\\", "/")
            # 相对路径优先：绝对路径只显示最后两段，避免泄露节点目录结构
            if text.startswith("/") or (len(text) > 2 and text[1] == ":"):
                parts = [part for part in text.split("/") if part]
                text = "/".join(parts[-2:]) if parts else text
            return text[:_COMMAND_SUMMARY_MAX]
    return ""


# 实例级 SSE 事件字段别名（tilacode 实例级流使用 ``properties`` 而非 ``data``）
_EVENT_PROPERTIES_KEY: str = "properties"


@dataclass
class ProgressUpdate:
    """中枢进度上报数据（ADR-055：含执行阶段与当前动作）。"""

    progress: int
    message: str
    tool_name: str | None = None
    stage: str = "executing"
    todos: list[dict[str, Any]] | None = None
    detail: dict[str, Any] | None = None


#: 执行阶段封闭枚举（ADR-055 D2）：前端据此映射中文标签
STAGE_PREPARING: str = "preparing"
STAGE_PLANNING: str = "planning"
STAGE_CODING: str = "coding"
STAGE_EDITING: str = "editing"
STAGE_RUNNING_COMMAND: str = "running_command"
STAGE_SEARCHING: str = "searching"
STAGE_TESTING: str = "testing"
STAGE_COMPACTING: str = "compacting"
STAGE_SUMMARIZING: str = "summarizing"
STAGE_IDLE: str = "idle"

#: 允许上报的阶段集合（供上游做防御式校验）
STAGES: frozenset[str] = frozenset(
    {
        STAGE_PREPARING,
        STAGE_PLANNING,
        STAGE_CODING,
        STAGE_EDITING,
        STAGE_RUNNING_COMMAND,
        STAGE_SEARCHING,
        STAGE_TESTING,
        STAGE_COMPACTING,
        STAGE_SUMMARIZING,
        STAGE_IDLE,
    }
)

#: 工具名 → 阶段映射（未列出者为 coding）
_TOOL_STAGE: dict[str, str] = {
    "edit": STAGE_EDITING,
    "write": STAGE_EDITING,
    "apply_patch": STAGE_EDITING,
    "bash": STAGE_RUNNING_COMMAND,
    "shell": STAGE_RUNNING_COMMAND,
    "grep": STAGE_SEARCHING,
    "glob": STAGE_SEARCHING,
    "read": STAGE_SEARCHING,
    "webfetch": STAGE_SEARCHING,
    "websearch": STAGE_SEARCHING,
    "todowrite": STAGE_CODING,
}

#: 命令摘要最大长度（message 中的命令片段）
_COMMAND_SUMMARY_MAX: int = 60
#: 当前动作描述最大长度（对齐中枢 String(500) 与节点侧截断 120）
MESSAGE_MAX_CHARS: int = 120
# ADR-063 M2：轨迹流式分段阈值（思考/正文增量攒到该长度即出一行，保证实时观感）
_TRACE_STREAM_CHARS: int = 1200
#: 轨迹工具结果摘要最大长度
_TOOL_RESULT_SUMMARY_MAX: int = 400
#: 轨迹状态下 part-id 登记数上限（防御：ended 丢失时避免字典无限增长）
_TRACE_PART_ID_LIMIT: int = 128


def is_permission_event(event: dict[str, Any]) -> bool:
    """判断是否为权限请求事件（由 bridge 兜底回复，不产生进度）。"""
    return isinstance(event, dict) and event.get("type") == _EVENT_PERMISSION_ASKED


class EventTranslator:
    """把 tilacode SSE 事件翻译为 ProgressUpdate。

    内部维护两个进度源：

    1. 基于事件计数的基础进度：session 就绪 10、阶段开始至少 20、
       工具/文本事件按计数小步推进（+2）封顶 90，完成置 100。
    2. 基于 todo 列表的真实进度：收到 ``todo.updated`` 后按
       ``completed / total`` 计算；当 todo 列表非空时，上报进度取
       两者较大值，避免 todo 初始阶段进度回退。
    """

    def __init__(self, task_id: str) -> None:
        """初始化翻译器。

        Args:
            task_id: 关联的中枢任务 ID，便于日志追踪。
        """
        self._task_id = task_id
        self._event_progress = 0
        self._todo_progress: int | None = None
        self._todos: list[dict[str, Any]] = []
        # ADR-055：当前执行阶段/动作/工具，供进度上报与静默期心跳复用
        self._stage: str = STAGE_IDLE
        self._message: str = ""
        self._tool_name: str | None = None
        # 压缩前的阶段（compaction.ended 后恢复）
        self._stage_before_compaction: str = STAGE_CODING
        # ADR-063 M2：轨迹分类状态——思考/正文分段的增量暂存与已上行数
        # （键为 part id：reasoningID / textID），以及 callID → 工具名关联表
        # （tool.success/failed 事件不携带工具名，需回查 tool.called）
        self._trace_pending_text: dict[str, str] = {}
        self._trace_emitted_chars: dict[str, int] = {}
        self._trace_tool_names: dict[str, str] = {}

    @property
    def task_id(self) -> str:
        """返回关联的任务 ID。"""
        return self._task_id

    @property
    def stage(self) -> str:
        """返回当前执行阶段（ADR-055 封闭枚举之一）。"""
        return self._stage

    @property
    def message(self) -> str:
        """返回最近一次当前动作描述（已截断至 MESSAGE_MAX_CHARS）。"""
        return self._message

    @property
    def tool_name(self) -> str | None:
        """返回当前工具名（无工具上下文时为 None）。"""
        return self._tool_name

    @property
    def progress(self) -> int:
        """返回当前综合进度值。

        有非空 todo 列表时取 ``max(事件进度, todo 进度)``；否则使用事件进度。
        """
        if self._todo_progress is not None and self._todos:
            return max(self._event_progress, self._todo_progress)
        return self._event_progress

    @property
    def todos(self) -> list[dict[str, Any]]:
        """返回当前 todo 列表。"""
        return self._todos

    def mark_session_ready(self) -> None:
        """标记 session 就绪：事件进度提升到初始值 10（不回退）。"""
        self._event_progress = max(self._event_progress, _PROGRESS_READY)

    def mark_complete(self) -> int:
        """标记任务完成：进度置为 100 并返回。"""
        self._event_progress = _PROGRESS_COMPLETE
        self._todo_progress = _PROGRESS_COMPLETE
        return _PROGRESS_COMPLETE

    def _advance(self) -> int:
        """小步推进事件进度（+2）并封顶 90，返回推进后的值。"""
        self._event_progress = min(self._event_progress + _PROGRESS_ADVANCE, _PROGRESS_CAP)
        return self._event_progress

    def _update_todos(self, data: dict[str, Any]) -> None:
        """解析 ``todo.updated`` 事件并更新内部 todo 进度。"""
        todos = data.get("todos")
        if not isinstance(todos, list):
            return
        self._todos = [todo for todo in todos if isinstance(todo, dict)]
        if not self._todos:
            self._todo_progress = None
            return
        completed = sum(1 for todo in self._todos if todo.get("status") == _TODO_STATUS_COMPLETED)
        self._todo_progress = max(0, min(100, -(-completed * 100 // len(self._todos))))

    def translate(self, event: dict[str, Any]) -> ProgressUpdate | None:
        """将单个 tilacode 事件翻译为进度上报。

        同时兼容 Session 级 SSE（字段名为 ``data``）与实例级 SSE
        （字段名为 ``properties``）两种事件格式。

        Args:
            event: SSE 事件 JSON 字典，形如 ``{id, type, data: {...}}``
                或 ``{id, type, properties: {...}}``。

        Returns:
            需要上报时返回 ProgressUpdate；权限事件、未知类型或数据异常返回 None。
        """
        if not isinstance(event, dict) or is_permission_event(event):
            return None
        event_type = event.get("type")
        data = event.get("data")
        if not isinstance(data, dict):
            data = event.get(_EVENT_PROPERTIES_KEY)
        data = data if isinstance(data, dict) else {}

        if event_type == _EVENT_TODO_UPDATED:
            # todo.updated 的 payload 可能是 {todos}（Session 级）或
            # {sessionID, todos}（实例级），统一提取 todos 字段处理。
            self._update_todos(data)
            self._stage = self._stage if self._stage != STAGE_IDLE else STAGE_CODING
            self._message = self._todos_message()
            return ProgressUpdate(
                progress=self.progress,
                message=self._message or "待办列表更新",
                stage=self._stage,
                todos=self._todos or None,
            )
        if event_type == _EVENT_STEP_STARTED:
            # 首个阶段开始时提升到 20，之后维持当前进度不回退
            self._event_progress = max(self._event_progress, _PROGRESS_STEP_STARTED)
            agent = data.get("agent")
            self._stage = STAGE_PLANNING if agent == "plan" else STAGE_CODING
            self._message = "开始执行阶段"
            self._tool_name = None
            return ProgressUpdate(
                progress=self.progress,
                message=self._message,
                stage=self._stage,
                todos=self._todos or None,
            )
        if event_type == _EVENT_TOOL_CALLED:
            name = self._tool_name_of(data)
            self._tool_name = name
            self._stage = _TOOL_STAGE.get(name, STAGE_CODING)
            self._message = self._tool_activity_message(name, data.get("input"))
            if name == "todowrite":
                # 待办兜底提取（2026-10-06 实测修复③）：tilacode 升级后
                # ``todo.updated`` 事件不再出现在实例级 SSE 流（节点
                # todo_updated_received 恒 0、tasks.todos 恒空），但 todowrite
                # 工具调用的 ``input.todos`` 即完整待办清单，且会话级
                # tool.called 事件稳定可达（轨迹行即源于此）——直接提取。
                self._update_todos(data.get("input") or {})
                return ProgressUpdate(
                    progress=self.progress,
                    message=self._todos_message() or self._message,
                    tool_name=name,
                    stage=self._stage,
                    todos=self._todos or None,
                )
            return ProgressUpdate(
                progress=self._advance(),
                message=self._message,
                tool_name=name,
                stage=self._stage,
                todos=self._todos or None,
            )
        if event_type == _EVENT_TOOL_PROGRESS:
            # 运行中工具的中间状态：不改进度，仅刷新「仍在动」的信号
            name = self._tool_name or "unknown"
            self._message = f"正在执行 {name}"
            return ProgressUpdate(
                progress=self.progress,
                message=self._message,
                tool_name=name,
                stage=self._stage,
                todos=self._todos or None,
            )
        if event_type == _EVENT_TOOL_SUCCESS:
            name = self._tool_name_of(data)
            self._tool_name = name
            self._stage = _TOOL_STAGE.get(name, STAGE_CODING)
            self._message = f"完成 {name}"
            return ProgressUpdate(
                progress=self._advance(),
                message=self._message,
                tool_name=name,
                stage=self._stage,
                todos=self._todos or None,
            )
        if event_type == _EVENT_TOOL_FAILED:
            # 失败判定由 bridge 处理 step.failed，这里不改状态仅上报
            name = self._tool_name_of(data)
            self._tool_name = name
            self._message = f"工具失败 {name}：{self._error_summary(data)}"
            return ProgressUpdate(
                progress=self.progress,
                message=self._message[:MESSAGE_MAX_CHARS],
                tool_name=name,
                stage=self._stage,
                todos=self._todos or None,
            )
        if event_type == _EVENT_TEXT_ENDED:
            summary = self._text_summary(data)
            if summary:
                self._message = summary
            return ProgressUpdate(
                progress=self._advance(),
                message=self._message,
                stage=self._stage,
                todos=self._todos or None,
            )
        if event_type == _EVENT_RETRIED:
            attempt = data.get("attempt")
            self._message = f"模型请求重试第 {attempt} 次" if attempt else "模型请求重试"
            return ProgressUpdate(
                progress=self.progress,
                message=self._message,
                stage=self._stage,
                todos=self._todos or None,
            )
        if event_type == _EVENT_COMPACTION_STARTED:
            if self._stage != STAGE_COMPACTING:
                self._stage_before_compaction = self._stage
            self._stage = STAGE_COMPACTING
            self._message = "正在压缩上下文"
            return ProgressUpdate(
                progress=self.progress,
                message=self._message,
                stage=self._stage,
                todos=self._todos or None,
            )
        if event_type == _EVENT_COMPACTION_ENDED:
            self._stage = self._stage_before_compaction
            self._message = "上下文压缩完成，继续执行"
            return ProgressUpdate(
                progress=self.progress,
                message=self._message,
                stage=self._stage,
                todos=self._todos or None,
            )
        if event_type == _EVENT_STEP_ENDED:
            self._advance()
            detail = self._step_detail(data)
            self._message = "阶段完成"
            return ProgressUpdate(
                progress=self.progress,
                message=self._message,
                stage=self._stage,
                todos=self._todos or None,
                detail=detail,
            )
        return None

    def translate_trace(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """将单个 tilacode 事件分类为会话轨迹行（ADR-063 M2，旁路通道）。

        与 :meth:`translate`（进度主链路）独立：本方法只产出
        ``{"kind", "payload"}`` 行（seq 由 ``AgentTraceCollector`` 统一分配），
        覆盖 reasoning（思考片段，增量攒批 + ended 收尾）、text（正文片段）、
        tool（调用/成功/失败）。其余事件返回空列表。

        Args:
            event: SSE 事件 JSON 字典（与 :meth:`translate` 同格式）。

        Returns:
            轨迹行列表（可为空）。
        """
        if not isinstance(event, dict):
            return []
        event_type = event.get("type")
        data = event.get("data")
        if not isinstance(data, dict):
            data = event.get(_EVENT_PROPERTIES_KEY)
        data = data if isinstance(data, dict) else {}

        if event_type == _EVENT_REASONING_DELTA:
            return self._trace_stream_part(
                "reasoning", str(data.get("reasoningID") or ""), data.get("delta")
            )
        if event_type == _EVENT_REASONING_ENDED:
            return self._trace_finish_part(
                "reasoning", str(data.get("reasoningID") or ""), data.get("text")
            )
        if event_type == _EVENT_TEXT_DELTA:
            return self._trace_stream_part(
                "text", str(data.get("textID") or ""), data.get("delta")
            )
        if event_type == _EVENT_TEXT_ENDED:
            return self._trace_finish_part(
                "text", str(data.get("textID") or ""), data.get("text")
            )
        if event_type == _EVENT_TOOL_CALLED:
            name = self._tool_name_of(data)
            call_id = str(data.get("callID") or "")
            if call_id:
                if len(self._trace_tool_names) >= _TRACE_PART_ID_LIMIT:
                    self._trace_tool_names.pop(next(iter(self._trace_tool_names)))
                self._trace_tool_names[call_id] = name
            return [
                {
                    "kind": "tool",
                    "payload": {
                        "tool": name,
                        "phase": "called",
                        "call_id": call_id,
                        "target": _primary_target(data.get("input")),
                    },
                }
            ]
        if event_type in (_EVENT_TOOL_SUCCESS, _EVENT_TOOL_FAILED):
            call_id = str(data.get("callID") or "")
            name = self._trace_tool_names.pop(call_id, None) or self._tool_name_of(data)
            succeeded = event_type == _EVENT_TOOL_SUCCESS
            payload: dict[str, Any] = {"tool": name, "phase": "success" if succeeded else "failed"}
            if call_id:
                payload["call_id"] = call_id
            if succeeded:
                summary = self._tool_result_summary(data)
                if summary:
                    payload["summary"] = summary
                exit_code = self._tool_exit_code(data)
                if exit_code is not None:
                    payload["exit_code"] = exit_code
            else:
                payload["summary"] = self._error_summary(data)
            return [{"kind": "tool", "payload": payload}]
        return []

    def _trace_stream_part(self, kind: str, part_id: str, delta: Any) -> list[dict[str, Any]]:
        """累计思考/正文增量；攒到 ``_TRACE_STREAM_CHARS`` 即产出一行。

        已上行字符数记录在 ``_trace_emitted_chars``，供 ended 事件计算
        剩余尾巴，避免「ended 全文」与「已上行增量」重复。
        """
        if not part_id or not isinstance(delta, str) or not delta:
            return []
        pending = self._trace_pending_text.get(part_id, "") + delta
        if len(pending) < _TRACE_STREAM_CHARS:
            if (
                len(self._trace_pending_text) >= _TRACE_PART_ID_LIMIT
                and part_id not in self._trace_pending_text
            ):
                self._trace_pending_text.pop(next(iter(self._trace_pending_text)))
            self._trace_pending_text[part_id] = pending
            return []
        self._trace_pending_text[part_id] = ""
        emitted = self._trace_emitted_chars.get(part_id, 0) + len(pending)
        self._trace_emitted_chars[part_id] = emitted
        return [{"kind": kind, "payload": {"text": pending}}]

    def _trace_finish_part(self, kind: str, part_id: str, full_text: Any) -> list[dict[str, Any]]:
        """part 结束：按已上行数计算剩余尾巴并清理登记（防内存增长）。"""
        self._trace_pending_text.pop(part_id, None)
        emitted = self._trace_emitted_chars.pop(part_id, 0)
        if not isinstance(full_text, str) or len(full_text) <= emitted:
            return []
        return [{"kind": kind, "payload": {"text": full_text[emitted:]}}]

    @staticmethod
    def _tool_result_summary(data: dict[str, Any]) -> str:
        """防御式提取工具成功结果的文本摘要（截断到 _TOOL_RESULT_SUMMARY_MAX）。

        ``tool.success`` 的 ``content`` 为 ToolOutput 内容（字符串或
        ``{type: "text", text}`` 块数组），取文本拼接后截断。
        """
        content = data.get("content")
        parts: list[str] = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item, str):
                    parts.append(item)
        joined = "\n".join(part for part in parts if part).strip()
        return joined[:_TOOL_RESULT_SUMMARY_MAX]

    @staticmethod
    def _tool_exit_code(data: dict[str, Any]) -> int | None:
        """防御式提取命令类工具的退出码（structured.exit / exitCode / exit_code）。"""
        structured = data.get("structured")
        if not isinstance(structured, dict):
            return None
        for key in ("exit", "exitCode", "exit_code"):
            value = structured.get(key)
            if isinstance(value, int):
                return value
        return None

    def _todos_message(self) -> str:
        """从 todo 列表提取「正在做什么」（in_progress 项内容）。"""
        for todo in self._todos:
            if todo.get("status") == _TODO_STATUS_IN_PROGRESS:
                content = todo.get("content")
                if isinstance(content, str) and content:
                    return content[:MESSAGE_MAX_CHARS]
        return ""

    @staticmethod
    def _tool_name_of(data: dict[str, Any]) -> str:
        """防御式提取工具名。

        真实 schema（``session.next.tool.*``）字段为 ``tool: string``；
        兼容旧格式（``data.name``）与异常结构，缺失时返回 ``unknown``。
        """
        name = data.get("tool")
        if not isinstance(name, str) or not name:
            fallback = data.get("name")
            name = fallback if isinstance(fallback, str) and fallback else None
        return name or "unknown"

    @staticmethod
    def _tool_activity_message(name: str, tool_input: Any) -> str:
        """按工具名与输入生成人类可读的当前动作描述（脱敏 + 截断）。

        只取主参数的**相对路径或命令片段**，不拼接完整绝对路径，避免把节点
        本地目录结构泄露到中枢展示层。
        """
        target = _primary_target(tool_input)
        if name in ("edit", "write", "apply_patch", "read"):
            return f"正在处理 {target}" if target else "正在处理文件"
        if name in ("bash", "shell"):
            return f"正在执行 {target}" if target else "正在执行命令"
        if name in ("grep", "glob"):
            return f"正在检索 {target}" if target else "正在检索代码"
        if name in ("webfetch", "websearch"):
            return "正在查询外部资料"
        if name == "todowrite":
            return "正在更新任务计划"
        return f"正在执行 {name}" + (f"：{target}" if target else "")

    def _step_detail(self, data: dict[str, Any]) -> dict[str, Any] | None:
        """提取 step.ended 的可观测明细（tokens/cost/files，ADR-055 P1-8）。"""
        detail: dict[str, Any] = {}
        tokens = data.get("tokens")
        if isinstance(tokens, dict):
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            detail["tokens"] = {
                "input": tokens.get("input"),
                "output": tokens.get("output"),
                "reasoning": tokens.get("reasoning"),
                "cache_read": cache.get("read"),
                "cache_write": cache.get("write"),
            }
        cost = data.get("cost")
        if isinstance(cost, (int, float)):
            detail["cost"] = float(cost)
        files = data.get("files")
        if isinstance(files, list):
            detail["files"] = [str(item) for item in files if isinstance(item, str)][:50]
        return detail or None

    @staticmethod
    def _error_summary(data: dict[str, Any]) -> str:
        """防御式提取错误事件的摘要文本。"""
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("name")
            if message:
                return str(message)[:_TEXT_SUMMARY_MAX]
        message = data.get("message")
        return str(message)[:_TEXT_SUMMARY_MAX] if message else "unknown"

    @staticmethod
    def _text_summary(data: dict[str, Any]) -> str:
        """提取最后文本片段摘要，截断到 120 字符。

        兼容 ``data.text`` 与 ``data.part.text`` 两种结构，缺字段时返回空串。
        """
        text = data.get("text")
        if not text:
            part = data.get("part")
            if isinstance(part, dict):
                text = part.get("text")
        return str(text)[:_TEXT_SUMMARY_MAX] if text else ""
