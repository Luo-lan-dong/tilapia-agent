"""中枢编排器待办服务（ADR-061 M5d / ADR-067 待办先行）。

`todo_write` 技能与只读端点共用的读写层，约束集中在此：

- content 去空白后 1..500 字符（非法**报错**而非静默截断）；
- 单 owner（任务或会话）条数上限 50（超出报错）；
- ``seq`` owner 内从 1 递增，新增一律追加在末尾；
- 状态只允许 ``pending`` / ``in_progress`` / ``completed``，``completed_at`` 随状态同步；
- 取值/更新一律带 owner 过滤（防止跨任务/跨会话误改）。

Owner 维度（ADR-067 待办先行）：
- 有任务 → 挂任务（``task_id``，原语义不变）；
- 无任务 → 挂构建会话（``session_id``），``create_task`` 成功后由技能层迁移到新任务。
  解决「待办依赖任务、任务依赖三件套」的鸡生蛋：收到需求第一件事即可列全计划。

与 `tasks.todos`（节点上报）**物理分离**：本服务只读写下表，绝不触碰该 JSONB 列。
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_building_session import TaskBuildingSession
from app.models.task_orchestrator_todo import (
    ORCHESTRATOR_TODO_CONTENT_MAX,
    ORCHESTRATOR_TODO_MAX_ITEMS,
    ORCHESTRATOR_TODO_STATUSES,
    TaskOrchestratorTodo,
)

logger = logging.getLogger(__name__)


class OrchestratorTodoError(Exception):
    """编排器待办操作失败（参数/上限/不存在），消息可直接回传 LLM。"""


def _normalize_content(raw: Any) -> str:
    """校验并规整待办内容（去空白、非空、长度上限）。"""
    content = str(raw).strip() if raw is not None else ""
    if not content:
        raise OrchestratorTodoError("待办内容不能为空。")
    if len(content) > ORCHESTRATOR_TODO_CONTENT_MAX:
        raise OrchestratorTodoError(
            f"待办内容过长（{len(content)} 字符 > {ORCHESTRATOR_TODO_CONTENT_MAX}）。"
        )
    return content


def _parse_todo_id(raw: Any) -> uuid.UUID:
    """解析待办 ID（非法值直接报错，便于 LLM 自我纠正）。"""
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise OrchestratorTodoError(f"待办 ID 非法：{raw}") from exc


def _validate_owner(
    task_id: uuid.UUID | None, session_id: uuid.UUID | None
) -> None:
    """owner 互斥校验：task_id 与 session_id 恰好传其一。"""
    if (task_id is None) == (session_id is None):
        raise OrchestratorTodoError("待办 owner 非法：task_id 与 session_id 必须恰好传其一。")


def _owner_where(task_id: uuid.UUID | None, session_id: uuid.UUID | None):
    """owner 过滤条件（task_id 优先维度，其次 session_id）。"""
    if task_id is not None:
        return TaskOrchestratorTodo.task_id == task_id
    return TaskOrchestratorTodo.session_id == session_id


async def list_todos(
    session: AsyncSession,
    task_id: uuid.UUID | None = None,
    *,
    session_id: uuid.UUID | None = None,
) -> list[TaskOrchestratorTodo]:
    """按 ``seq`` 升序返回 owner（任务或会话）的中枢待办。"""
    _validate_owner(task_id, session_id)
    result = await session.execute(
        select(TaskOrchestratorTodo)
        .where(_owner_where(task_id, session_id))
        .order_by(TaskOrchestratorTodo.seq.asc())
    )
    return list(result.scalars().all())


async def add_todos(
    session: AsyncSession,
    task_id: uuid.UUID | None = None,
    contents: list[Any] | None = None,
    *,
    session_id: uuid.UUID | None = None,
) -> list[TaskOrchestratorTodo]:
    """追加待办（保持传入顺序），返回新增行。"""
    _validate_owner(task_id, session_id)
    if not contents:
        raise OrchestratorTodoError("items 不能为空。")
    normalized = [_normalize_content(item) for item in contents]

    count = await session.scalar(
        select(func.count()).select_from(TaskOrchestratorTodo).where(
            _owner_where(task_id, session_id)
        )
    )
    existing = int(count or 0)
    if existing + len(normalized) > ORCHESTRATOR_TODO_MAX_ITEMS:
        raise OrchestratorTodoError(
            f"待办条数超限：已有 {existing} 条，本次新增 {len(normalized)} 条，"
            f"上限 {ORCHESTRATOR_TODO_MAX_ITEMS} 条（请先完成/删除部分条目）。"
        )

    max_seq = await session.scalar(
        select(func.max(TaskOrchestratorTodo.seq)).where(_owner_where(task_id, session_id))
    )
    next_seq = int(max_seq or 0) + 1
    rows = [
        TaskOrchestratorTodo(
            task_id=task_id,
            session_id=session_id,
            seq=next_seq + offset,
            content=content,
            status="pending",
            source="orchestrator",
        )
        for offset, content in enumerate(normalized)
    ]
    session.add_all(rows)
    await session.commit()
    for row in rows:
        await session.refresh(row)
    logger.info(
        "orchestrator_todos_added",
        extra={
            "event": "orchestrator_todos_added",
            "task_id": str(task_id) if task_id else None,
            "session_id": str(session_id) if session_id else None,
            "added": len(rows),
            "total": existing + len(rows),
        },
    )
    return rows


async def _get_todo(
    session: AsyncSession,
    task_id: uuid.UUID | None,
    session_id: uuid.UUID | None,
    todo_id: uuid.UUID,
) -> TaskOrchestratorTodo:
    """按 owner 维度取待办行（跨 owner ID 一律视为不存在）。"""
    row = await session.get(TaskOrchestratorTodo, todo_id)
    if row is None:
        raise OrchestratorTodoError(f"待办不存在：{todo_id}")
    if task_id is not None and row.task_id != task_id:
        raise OrchestratorTodoError(f"待办不存在：{todo_id}")
    if task_id is None and session_id is not None and row.session_id != session_id:
        raise OrchestratorTodoError(f"待办不存在：{todo_id}")
    return row


async def update_todo(
    session: AsyncSession,
    task_id: uuid.UUID | None = None,
    todo_id: uuid.UUID | None = None,
    *,
    session_id: uuid.UUID | None = None,
    content: Any = None,
    status: Any = None,
) -> TaskOrchestratorTodo:
    """更新内容与/或状态（``completed_at`` 随状态同步）。"""
    _validate_owner(task_id, session_id)
    if todo_id is None:
        raise OrchestratorTodoError("待办 ID 非法：None")
    row = await _get_todo(session, task_id, session_id, todo_id)
    if content is not None:
        row.content = _normalize_content(content)
    if status is not None:
        normalized_status = str(status).strip()
        if normalized_status not in ORCHESTRATOR_TODO_STATUSES:
            raise OrchestratorTodoError(
                f"状态非法：{status}（允许 {', '.join(ORCHESTRATOR_TODO_STATUSES)}）"
            )
        row.status = normalized_status
        row.completed_at = datetime.now(UTC) if normalized_status == "completed" else None
    await session.commit()
    await session.refresh(row)
    logger.info(
        "orchestrator_todo_updated",
        extra={
            "event": "orchestrator_todo_updated",
            "task_id": str(task_id) if task_id else None,
            "session_id": str(session_id) if session_id else None,
            "todo_id": str(row.id),
            "status": row.status,
            "seq": row.seq,
        },
    )
    return row


async def remove_todo(
    session: AsyncSession,
    task_id: uuid.UUID | None = None,
    todo_id: uuid.UUID | None = None,
    *,
    session_id: uuid.UUID | None = None,
) -> None:
    """删除单条待办（``seq`` 不重排，避免前端顺序跳动）。"""
    _validate_owner(task_id, session_id)
    if todo_id is None:
        raise OrchestratorTodoError("待办 ID 非法：None")
    row = await _get_todo(session, task_id, session_id, todo_id)
    await session.execute(delete(TaskOrchestratorTodo).where(TaskOrchestratorTodo.id == row.id))
    await session.commit()
    logger.info(
        "orchestrator_todo_removed",
        extra={
            "event": "orchestrator_todo_removed",
            "task_id": str(task_id) if task_id else None,
            "session_id": str(session_id) if session_id else None,
            "todo_id": str(todo_id),
        },
    )


async def reset_todos(
    session: AsyncSession,
    task_id: uuid.UUID | None = None,
    contents: list[Any] | None = None,
    *,
    session_id: uuid.UUID | None = None,
) -> list[TaskOrchestratorTodo]:
    """清空 owner 名下全部待办后重写新清单（ADR-067 修订：计划重建语义）。

    供编排 AI 在「需求确认后细化计划」「计划整体变更」时使用——替代反复
    add 叠加导致新旧两套清单混排。seq 从 1 重新编号。

    Raises:
        OrchestratorTodoError: items 缺失或非法时抛出。
    """
    _validate_owner(task_id, session_id)
    if not isinstance(contents, list) or not contents:
        raise OrchestratorTodoError("action=reset 需要提供 items（字符串数组，整份新清单）。")
    await session.execute(delete(TaskOrchestratorTodo).where(_owner_where(task_id, session_id)))
    await session.commit()
    logger.info(
        "orchestrator_todos_reset",
        extra={
            "event": "orchestrator_todos_reset",
            "task_id": str(task_id) if task_id else None,
            "session_id": str(session_id) if session_id else None,
        },
    )
    return await add_todos(session, task_id, contents, session_id=session_id)


async def reset_in_progress(
    session: AsyncSession,
    task_id: uuid.UUID | None = None,
    *,
    session_id: uuid.UUID | None = None,
) -> int:
    """把 owner 名下 ``in_progress`` 的待办重置回 ``pending``（中断/取消时调用）。

    Returns:
        重置的条数。
    """
    _validate_owner(task_id, session_id)
    result = await session.execute(
        select(TaskOrchestratorTodo).where(
            _owner_where(task_id, session_id),
            TaskOrchestratorTodo.status == "in_progress",
        )
    )
    rows = list(result.scalars().all())
    if not rows:
        return 0
    for row in rows:
        row.status = "pending"
    await session.commit()
    logger.info(
        "orchestrator_todos_in_progress_reset",
        extra={
            "event": "orchestrator_todos_in_progress_reset",
            "task_id": str(task_id) if task_id else None,
            "session_id": str(session_id) if session_id else None,
            "reset": len(rows),
        },
    )
    return len(rows)


async def reset_in_progress_for_chat_session(
    session: AsyncSession, chat_session_id: uuid.UUID
) -> int:
    """按编排会话解析 owner 并重置 in_progress 待办（对话流被中断时调用）。

    Owner 解析与 ``todo_write`` 技能一致：会话绑任务 → 挂任务；未绑 → 挂会话。

    Returns:
        重置的条数；会话不存在时为 0。
    """
    chat_session = await session.get(TaskBuildingSession, chat_session_id)
    if chat_session is None:
        return 0
    if chat_session.task_id is not None:
        return await reset_in_progress(session, task_id=chat_session.task_id)
    return await reset_in_progress(session, session_id=chat_session.id)


async def migrate_session_todos(
    session: AsyncSession, session_id: uuid.UUID, task_id: uuid.UUID
) -> int:
    """把会话名下未挂任务的待办迁移到新任务（ADR-067 待办先行，create_task 后调用）。

    旁路原则：调用方捕获异常，迁移失败不阻塞创建任务。

    Returns:
        迁移的待办条数。
    """
    result = await session.execute(
        select(TaskOrchestratorTodo).where(
            TaskOrchestratorTodo.session_id == session_id,
            TaskOrchestratorTodo.task_id.is_(None),
        )
    )
    rows = list(result.scalars().all())
    if not rows:
        return 0
    # 迁移后 seq 拼接到任务既有待办之后（保持顺序连贯）
    max_seq = await session.scalar(
        select(func.max(TaskOrchestratorTodo.seq)).where(TaskOrchestratorTodo.task_id == task_id)
    )
    base = int(max_seq or 0)
    for offset, row in enumerate(rows):
        row.task_id = task_id
        row.session_id = None
        row.seq = base + offset + 1
    await session.commit()
    logger.info(
        "orchestrator_todos_migrated",
        extra={
            "event": "orchestrator_todos_migrated",
            "session_id": str(session_id),
            "task_id": str(task_id),
            "migrated": len(rows),
        },
    )
    return len(rows)


def render_lines(rows: list[TaskOrchestratorTodo]) -> str:
    """把待办渲染为回传 LLM 的多行文本（含状态标记与稳定 ID）。"""
    marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
    return "\n".join(
        f"{marks.get(row.status, '[?]')} {row.seq}. {row.content} (id={row.id})" for row in rows
    )
