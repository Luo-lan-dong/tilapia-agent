"""节点 Agent 会话轨迹采集器（ADR-063 M2）。

把 ``EventTranslator.translate_trace`` 产出的轨迹行（reasoning/text/tool）
按任务缓存、批量上报中枢 ``POST /v1/tasks/{id}/agent-trace``：

- **seq 统一分配**：任务内从 1 单调递增（含桥接层 status 行），中枢以
  ``(task_id, seq)`` 幂等去重；
- **批量**：攒批 32 行或距上次成功上报 1 秒即触发一次后台 flush（不阻塞
  SSE 消费循环），HTTP 发送单批上限 200 行；
- **重试**：上报失败把整批按原序塞回队首，等待下一次触发（事件驱动 +
  桥接层心跳兜底）重试；中枢按 seq 幂等，重发安全；
- **背压**：待发队列上限 1024 行，满时丢最老的行并计数，恢复后补一条
  ``status`` 行告知丢弃数量；
- **脱敏**：``sk-`` 形态串、常见凭据赋值（api_key= / token= / password= 等）、
  Bearer 头统一替换为 ``***``；敏感键名的值整体打码；
- **截断**：单个字符串值按 UTF-8 字节保留头 6144 B + 尾 512 B（与中枢
  8 KB 落库红线对齐，标注【截断】）。

整条链路是**旁路**：任何异常只记 warning，绝不影响任务执行与完成判定。
"""

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

#: 攒批触发行数（ADR-063 M2：32 行或 1 秒）
BATCH_TRIGGER_ROWS: int = 32
#: 攒批触发间隔（秒）
FLUSH_INTERVAL_SECONDS: float = 1.0
#: 单次 POST 最大行数（积压排水时按此分块）
MAX_POST_ROWS: int = 200
#: 待发队列上限（背压红线），满时丢最老
MAX_PENDING_ROWS: int = 1024
#: 单字符串值保留头部字节数（与中枢 8KB 落库上限对齐）
_MAX_TEXT_HEAD_BYTES: int = 6144
#: 单字符串值保留尾部字节数
_MAX_TEXT_TAIL_BYTES: int = 512
#: 超过「头 + 尾 + 余量」即视为需要截断
_MAX_TEXT_TOTAL_BYTES: int = _MAX_TEXT_HEAD_BYTES + _MAX_TEXT_TAIL_BYTES + 64
#: 截断标记
_TRUNCATION_MARKER: str = "\n…【截断】…\n"

# 脱敏规则（ADR-063 M2）：顺序应用，命中即替换为 ***（Bearer 头须在
# 凭据赋值规则之前，否则值先被赋值规则吞掉前一个词导致漏网）
_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Bearer 认证头
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "***"),
    # sk- 形态的 API 密钥（OpenAI/DeepSeek 等通用前缀）
    (re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9_-]{6,}"), "***"),
    # 常见凭据赋值：api_key=xxx / token: xxx / password=xxx（含引号值）
    (
        re.compile(
            r"(?i)\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|secret|"
            r"password|passwd|pwd|authorization|token)\b"
            r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
        ),
        r"\1\2***",
    ),
)

#: 敏感键名（整键打码）；全词匹配避免误伤 ``tokens`` 这类用量字段
_SENSITIVE_KEY: re.Pattern[str] = re.compile(
    r"(?i)^(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|secret|"
    r"password|passwd|pwd|authorization|credential|credentials)$"
)


def redact_text(text: str) -> str:
    """对单条文本应用脱敏规则（sk- 串 / 凭据赋值 / Bearer 头）。"""
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_payload(value: Any) -> Any:
    """递归脱敏 payload：字符串值过规则，敏感键名的值整体打码。"""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            key: (
                "***"
                if isinstance(key, str) and _SENSITIVE_KEY.match(key)
                else redact_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    return value


def cap_text(text: str) -> str:
    """按 UTF-8 字节截断超长文本：保留头尾并标注（读取侧无需再兜底）。"""
    raw = text.encode("utf-8")
    if len(raw) <= _MAX_TEXT_TOTAL_BYTES:
        return text
    head = raw[:_MAX_TEXT_HEAD_BYTES].decode("utf-8", errors="ignore")
    tail = raw[-_MAX_TEXT_TAIL_BYTES:].decode("utf-8", errors="ignore")
    return f"{head}{_TRUNCATION_MARKER}{tail}"


def cap_payload(value: Any) -> Any:
    """递归截断 payload 中的超长字符串（脱敏之后应用）。"""
    if isinstance(value, str):
        return cap_text(value)
    if isinstance(value, dict):
        return {key: cap_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [cap_payload(item) for item in value]
    return value


class AgentTraceCollector:
    """单任务的轨迹行采集器：seq 分配、攒批、重试、背压、脱敏、截断。

    生命周期与任务一致：``TaskBridge.execute_task`` 创建并在收尾时
    ``close()``；事件侧（SSE 消费循环）只做同步入队（快、不阻塞），
    实际 HTTP 上报由 ``flush`` 在后台任务或心跳/收尾时机执行。
    """

    def __init__(
        self,
        task_id: str,
        reporter: Callable[..., Awaitable[None]],
        *,
        start_seq: int = 0,
    ) -> None:
        """初始化采集器。

        Args:
            task_id: 中枢任务 ID（上报路径参数）。
            reporter: 上报回调（``HubClient.report_agent_trace`` 同签名）。
            start_seq: seq 基线（ADR-064 M2 续聊轮接续）：首行 seq 为
                ``start_seq + 1``，与中枢下发的 ``last_seq`` 衔接；默认 0
                保持既有「任务内从 1 起」行为。
        """
        self._task_id = task_id
        self._reporter = reporter
        self._session_id: str | None = None
        self._pending: deque[dict[str, Any]] = deque()
        self._seq = start_seq
        self._dropped = 0
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._last_flush_at = 0.0
        self._closed = False
        self._logger = logging.getLogger(__name__)

    def configure_session(self, session_id: str | None) -> None:
        """登记该任务对应的 tilacode 会话 ID（随上报元数据透传中枢）。"""
        self._session_id = session_id

    def emit_status(
        self, *, stage: str, message: str, extra: dict[str, Any] | None = None
    ) -> None:
        """入队一条 ``status`` 轨迹行（桥接层启动/结束行，ADR-063 M1）。"""
        payload: dict[str, Any] = {"stage": stage, "message": message}
        if extra:
            payload.update(extra)
        self._enqueue("status", payload)
        self._schedule_flush_if_due()

    def emit_rows(self, rows: list[dict[str, Any]]) -> None:
        """入队翻译器产出的轨迹行（``{kind, payload}``，seq 在此统一分配）。"""
        for row in rows:
            kind = row.get("kind")
            payload = row.get("payload")
            if not isinstance(kind, str) or not isinstance(payload, dict):
                continue
            self._enqueue(kind, payload)
        self._schedule_flush_if_due()

    def _enqueue(self, kind: str, payload: dict[str, Any]) -> None:
        """分配 seq、脱敏/截断后入队；队列满丢最老并计数（背压）。"""
        if len(self._pending) >= MAX_PENDING_ROWS:
            self._pending.popleft()
            self._dropped += 1
        self._seq += 1
        self._pending.append(
            {
                "seq": self._seq,
                "kind": kind,
                "payload": cap_payload(redact_payload(payload)),
            }
        )

    def _schedule_flush_if_due(self) -> None:
        """攒批/间隔到期且有积压时调度一次后台 flush（不阻塞调用方）。"""
        if self._closed or not self._pending:
            return
        if self._flush_task is not None and not self._flush_task.done():
            return
        due = (
            len(self._pending) >= BATCH_TRIGGER_ROWS
            or (time.monotonic() - self._last_flush_at) >= FLUSH_INTERVAL_SECONDS
        )
        if due:
            self._flush_task = asyncio.create_task(self.flush())

    async def flush(self) -> None:
        """把积压行分批上报中枢；失败整批回队等待重试（绝不上抛）。

        ``_last_flush_at`` 仅在成功后推进：失败后只要再有事件（或心跳/
        收尾 flush）就会立即重试。锁保证桥接层直接调用与后台任务并发时
        不会重复发送同一批行。
        """
        async with self._lock:
            while self._pending:
                batch: list[dict[str, Any]] = []
                while self._pending and len(batch) < MAX_POST_ROWS:
                    batch.append(self._pending.popleft())
                try:
                    await self._reporter(
                        self._task_id,
                        node_id=None,
                        session_id=self._session_id,
                        rows=batch,
                    )
                except Exception as exc:  # noqa: BLE001 — 旁路上报，失败绝不影响任务
                    self._requeue(batch)
                    self._logger.warning(
                        "agent_trace_flush_failed",
                        extra={
                            "event": "agent_trace_flush_failed",
                            "task_id": self._task_id,
                            "row_count": len(batch),
                            "error": str(exc),
                        },
                    )
                    return
                self._last_flush_at = time.monotonic()
                if self._dropped:
                    dropped, self._dropped = self._dropped, 0
                    self._enqueue(
                        "status",
                        {"stage": "轨迹", "message": f"背压丢弃 {dropped} 条轨迹行"},
                    )

    def _requeue(self, batch: list[dict[str, Any]]) -> None:
        """上报失败的整批按原序塞回队首；放不下的（最老的）计入丢弃。"""
        space = MAX_PENDING_ROWS - len(self._pending)
        keep = batch[-space:] if space > 0 else []
        self._dropped += len(batch) - len(keep)
        for row in reversed(keep):
            self._pending.appendleft(row)

    async def close(self) -> None:
        """任务收尾：等待在途 flush 后做最终排水（此后不再调度新 flush）。"""
        self._closed = True
        task = self._flush_task
        if task is not None and not task.done():
            try:
                await task
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — flush 内部已兜底，此处双保险
                pass
        await self.flush()
