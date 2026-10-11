"""节点 Agent 会话轨迹镜像服务（ADR-063 M1）。

节点侧批量上报视图行，中枢只做**落库 + 读取**两件事：约束集中在此，端点与 SSE 只管搬运。

**为什么不在这里发事件**：SSE 的 ``node_trace`` 发布由 ingress 端点负责（本模块无网络/无事件依赖，
纯 DB 读写层）——服务层保持可单测、无副作用。

约束：
- ``kind`` 只允许 ``status/reasoning/text/tool/todo/usage``，非法行丢弃并计入 ``ignored``；
- ``(task_id, seq)`` 幂等：已存在（含同一批次内重复）的 seq 一律忽略，不报错、不覆盖；
- 单行 ``payload`` 序列化（``ensure_ascii=False``）超过 8 KB 时截断为「前 6144 字节 +
  后 1536 字节 + ``{"truncated": true}`` 标记」，且**结果仍是合法 JSON**；
- 单任务已有行数 ≥ 5000 时拒绝本次新增（全部计入 ``ignored``）并 warning；
- 插入遇到竞态冲突（另一路并发写入了相同 ``(task_id, seq)``）时幂等跳过，不抛错。
"""

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_agent_trace import (
    AGENT_TRACE_KINDS,
    AGENT_TRACE_MAX_ROWS_PER_TASK,
    AGENT_TRACE_PAYLOAD_MAX_BYTES,
    TaskAgentTrace,
)

logger = logging.getLogger(__name__)

#: 单行截断保留量（字节）：头部 6144 + 尾部 1536
_TRUNCATE_HEAD_BYTES = 6144
_TRUNCATE_TAIL_BYTES = 1536
#: 截断标记（占用尾部预算，保证截断后仍是合法 JSON）
_TRUNCATE_MARKER = '{"truncated":true}'
#: ``list_rows`` 单次返回上限
_MAX_LIST_LIMIT = 1000
#: ``list_rows`` 默认返回条数
_DEFAULT_LIST_LIMIT = 200


def _json_length(value: Any) -> int:
    """返回值的 JSON 序列化字节数（``ensure_ascii=False``，与落库口径一致）。"""
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _truncate_to_json_bytes(raw: str, max_bytes: int) -> str:
    """把 JSON 文本截到字节上限内，且保证结果仍是合法 JSON。

    策略：头部 6144 字节 + 尾部 1536 字节（按 UTF-8 字符边界回退，不切出半个汉字），
    中间填 ``{"truncated":true}``。返回的文本本身合法 JSON；内容为 str 时仍是完整
    JSON 字符串（可能被解码成替换字符）；内容为对象/数组时可能在头尾交界处无法解析，
    调用方（``_truncate_payload``）会据此回退为字符串包裹形式。
    """
    budget = max_bytes - len(_TRUNCATE_MARKER)
    head_budget = min(_TRUNCATE_HEAD_BYTES, budget)
    tail_budget = min(_TRUNCATE_TAIL_BYTES, max(0, budget - head_budget))

    raw_bytes = raw.encode("utf-8")
    head = raw_bytes[:head_budget].decode("utf-8", errors="ignore")
    tail = raw_bytes[len(raw_bytes) - tail_budget :].decode("utf-8", errors="ignore")
    return head + _TRUNCATE_MARKER + tail


def _truncate_payload(payload: Any) -> str:
    """序列化 payload（``ensure_ascii=False``）；超限时截断并标记 ``truncated``。

    **本函数只负责存储文本的形状**：超限时按文档口径直接返回「前 6144 字节 +
    标记 + 后 1536 字节」的原文拼接；拼接产物能否被 ``json.loads`` 解析由**读取侧**
    兜底（``_loads_payload`` / 端点），坏行绝不能让读接口 500。
    """
    raw = json.dumps(payload, ensure_ascii=False)
    if len(raw.encode("utf-8")) <= AGENT_TRACE_PAYLOAD_MAX_BYTES:
        return raw

    return _truncate_to_json_bytes(raw, AGENT_TRACE_PAYLOAD_MAX_BYTES)


def _coerce_seq(raw: Any) -> int | None:
    """解析 seq（仅接受整数，布尔不算）；非法返回 None。"""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def _coerce_kind(raw: Any) -> str | None:
    """解析 kind（只接受封闭集合内的字符串）；非法返回 None。"""
    if not isinstance(raw, str):
        return None
    kind = raw.strip()
    return kind if kind in AGENT_TRACE_KINDS else None


class TaskAgentTraceService:
    """节点会话轨迹的读写服务（ADR-063 M1）。

    用法（端点/测试均显式传会话，便于放进调用方事务）::

        service = TaskAgentTraceService()
        result = await service.append_rows(session, task_id, node_id, session_id, rows)
    """

    async def append_rows(
        self,
        session: AsyncSession,
        task_id: Any,
        node_id: str | None,
        session_id: str | None,
        rows: list[dict[str, Any]],
    ) -> dict[str, int]:
        """批量追加轨迹行，返回 ``{"accepted": n, "ignored": m}``。

        ``rows`` = ``[{"seq": int, "kind": str, "payload": dict}]``；非法 kind / 非法
        seq / 重复 ``(task_id, seq)`` / 超单任务上限的行全部计入 ``ignored``。
        """
        incoming = rows or []
        accepted = 0
        ignored = 0

        existing_count = await self.count_rows(session, task_id)
        if existing_count >= AGENT_TRACE_MAX_ROWS_PER_TASK:
            logger.warning(
                "agent_trace_rows_exceeded",
                extra={
                    "event": "agent_trace_rows_exceeded",
                    "task_id": str(task_id),
                    "existing": existing_count,
                    "limit": AGENT_TRACE_MAX_ROWS_PER_TASK,
                    "incoming": len(incoming),
                },
            )
            return {"accepted": 0, "ignored": len(incoming)}

        existing_seqs = set(
            (
                await session.scalars(
                    select(TaskAgentTrace.seq).where(TaskAgentTrace.task_id == task_id)
                )
            ).all()
        )

        pending: list[dict[str, Any]] = []
        seen_in_batch: set[int] = set()
        for raw_row in incoming:
            if not isinstance(raw_row, dict):
                ignored += 1
                continue
            seq = _coerce_seq(raw_row.get("seq"))
            kind = _coerce_kind(raw_row.get("kind"))
            if seq is None or kind is None:
                ignored += 1
                continue
            if seq in existing_seqs or seq in seen_in_batch:
                ignored += 1
                continue
            if existing_count + len(pending) >= AGENT_TRACE_MAX_ROWS_PER_TASK:
                # 单任务行数上限：剩下的整批拒绝（不静默覆盖，也不写半截）
                logger.warning(
                    "agent_trace_rows_exceeded",
                    extra={
                        "event": "agent_trace_rows_exceeded",
                        "task_id": str(task_id),
                        "existing": existing_count,
                        "limit": AGENT_TRACE_MAX_ROWS_PER_TASK,
                        "incoming": len(incoming),
                    },
                )
                # 已丢弃的（ignored）与准备写入的（pending，含本行）之外的全部计入忽略
                ignored = len(incoming) - len(pending)
                break
            seen_in_batch.add(seq)
            pending.append(
                {
                    "task_id": task_id,
                    "node_id": node_id,
                    "session_id": session_id,
                    "seq": seq,
                    "kind": kind,
                    "payload": _truncate_payload(raw_row.get("payload")),
                }
            )

        if pending:
            statement = pg_insert(TaskAgentTrace).values(pending)
            statement = statement.on_conflict_do_nothing(
                index_elements=[TaskAgentTrace.task_id, TaskAgentTrace.seq]
            )
            try:
                result = await session.execute(statement)
                await session.commit()
            except IntegrityError:
                # 外键（任务被并发删除）等硬冲突：回滚后整批忽略，绝不让上报打挂任务
                await session.rollback()
                logger.warning(
                    "agent_trace_insert_conflict",
                    extra={
                        "event": "agent_trace_insert_conflict",
                        "task_id": str(task_id),
                        "incoming": len(incoming),
                    },
                )
                return {"accepted": 0, "ignored": len(incoming)}
            accepted = int(result.rowcount or 0)
            ignored += len(pending) - accepted

        return {"accepted": accepted, "ignored": ignored}

    async def list_rows(
        self,
        session: AsyncSession,
        task_id: Any,
        node_id: str | None = None,
        after_seq: int | None = None,
        limit: int = _DEFAULT_LIST_LIMIT,
    ) -> dict[str, Any]:
        """按 ``seq`` 升序返回轨迹行，``{"rows": [...], "has_more": bool}``。

        ``has_more`` = 本次返回后是否还有更新（seq 更大）的行——多取一条来判断，
        避免额外 COUNT。
        """
        safe_limit = self._normalize_limit(limit)
        conditions = [TaskAgentTrace.task_id == task_id]
        if node_id is not None:
            conditions.append(TaskAgentTrace.node_id == node_id)
        if after_seq is not None:
            conditions.append(TaskAgentTrace.seq > after_seq)

        result = await session.scalars(
            select(TaskAgentTrace)
            .where(*conditions)
            .order_by(TaskAgentTrace.seq.asc())
            .limit(safe_limit + 1)
        )
        fetched = list(result.all())
        has_more = len(fetched) > safe_limit

        rows = [
            {
                "id": row.id,
                "task_id": str(row.task_id),
                "node_id": row.node_id,
                "session_id": row.session_id,
                "seq": row.seq,
                "kind": row.kind,
                "payload": self._loads_payload(row.payload),
                # 返回 datetime 本身（不是 isoformat 字符串）：端点 schema
                # ``AgentTraceRowOut.created_at`` 就是 datetime，由 Pydantic 统一序列化
                "created_at": row.created_at,
            }
            for row in fetched[:safe_limit]
        ]
        return {"rows": rows, "has_more": has_more}

    async def count_rows(self, session: AsyncSession, task_id: Any) -> int:
        """返回任务已有轨迹行数（单任务上限判定用）。"""
        count = await session.scalar(
            select(func.count())
            .select_from(TaskAgentTrace)
            .where(TaskAgentTrace.task_id == task_id)
        )
        return int(count or 0)

    @staticmethod
    def _normalize_limit(limit: Any) -> int:
        """夹取 limit 到 ``1..1000``（非法值回退默认）。"""
        if isinstance(limit, bool) or not isinstance(limit, int):
            return _DEFAULT_LIST_LIMIT
        return max(1, min(limit, _MAX_LIST_LIMIT))

    @staticmethod
    def _loads_payload(raw: str) -> Any:
        """反序列化 payload（截断/异常内容兜底为原始文本，读接口不因坏行 500）。"""
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return raw
