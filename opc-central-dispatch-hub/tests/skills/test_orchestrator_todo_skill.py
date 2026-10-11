"""中枢编排器待办技能测试（ADR-061 M5d）。

覆盖：六个动作的状态迁移与顺序、与节点待办**物理分离**、未绑定任务时明确失败、
上限与内容边界、只读端点的顺序与计数。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskStatus
from app.models.task_building_session import TaskBuildingSession
from app.models.task_orchestrator_todo import (
    ORCHESTRATOR_TODO_CONTENT_MAX,
    ORCHESTRATOR_TODO_MAX_ITEMS,
    TaskOrchestratorTodo,
)
from app.skills.base import SkillContext, SkillResult
from app.skills.orchestrator_todo import OrchestratorTodoSkill


def _result(events: list[dict]) -> SkillResult:
    """取技能结果事件。"""
    return events[-1]["data"]


async def _run(
    db_session: AsyncSession,
    args: dict,
    *,
    session_id: str | None = None,
    parent_task_id: str | None = None,
) -> tuple[SkillResult, list[dict]]:
    """执行技能并返回 (结果, 事件列表)。"""
    context = SkillContext(args=args, session_id=session_id, parent_task_id=parent_task_id)
    events = [event async for event in OrchestratorTodoSkill().execute(db_session, context)]
    return _result(events), events


@pytest.fixture
async def task_with_session(db_session: AsyncSession):
    """构造一个任务 + 关联构建会话（用例后清理）。"""
    task = Task(
        id=uuid.uuid4(),
        title="中枢待办测试任务",
        agent_type="programming",
        status=TaskStatus.PENDING.value,
        todos=[{"content": "节点上报的待办", "status": "pending"}],
    )
    session_row = TaskBuildingSession(
        id=uuid.uuid4(),
        title="中枢待办测试会话",
        task_id=task.id,
        mode="thinking",
    )
    db_session.add_all([task, session_row])
    await db_session.commit()
    yield task, session_row
    await db_session.execute(
        delete(TaskOrchestratorTodo).where(TaskOrchestratorTodo.task_id == task.id)
    )
    await db_session.execute(delete(TaskBuildingSession).where(TaskBuildingSession.id == session_row.id))
    await db_session.execute(delete(Task).where(Task.id == task.id))
    await db_session.commit()


async def test_add_then_list_keeps_order(
    db_session: AsyncSession, task_with_session
) -> None:
    """UT-M5d-1a：add 按传入顺序追加，seq 从 1 递增；list 按 seq 升序返回。"""
    task, session_row = task_with_session

    result, _ = await _run(
        db_session,
        {"action": "add", "items": ["规划任务", "实现功能", "自测"]},
        session_id=str(session_row.id),
    )

    assert result.success is True
    assert result.data["count"] == 3
    rows = (
        await db_session.execute(
            select(TaskOrchestratorTodo)
            .where(TaskOrchestratorTodo.task_id == task.id)
            .order_by(TaskOrchestratorTodo.seq)
        )
    ).scalars().all()
    assert [row.seq for row in rows] == [1, 2, 3]
    assert [row.content for row in rows] == ["规划任务", "实现功能", "自测"]
    assert {row.status for row in rows} == {"pending"}


async def test_state_transitions_and_remove(
    db_session: AsyncSession, task_with_session
) -> None:
    """UT-M5d-1b：start → in_progress；complete → completed + completed_at；update/remove 生效。"""
    _task, session_row = task_with_session
    await _run(
        db_session,
        {"action": "add", "items": ["第一步", "第二步"]},
        session_id=str(session_row.id),
    )
    rows = (
        await db_session.execute(
            select(TaskOrchestratorTodo).order_by(TaskOrchestratorTodo.seq)
        )
    ).scalars().all()
    first, second = rows[0], rows[1]

    started, _ = await _run(
        db_session, {"action": "start", "todo_id": str(first.id)}, session_id=str(session_row.id)
    )
    assert started.success is True
    await db_session.refresh(first)
    assert first.status == "in_progress"
    assert first.completed_at is None

    completed, _ = await _run(
        db_session,
        {"action": "complete", "todo_id": str(first.id)},
        session_id=str(session_row.id),
    )
    assert completed.success is True
    assert completed.data["completed"] == 1
    await db_session.refresh(first)
    assert first.status == "completed"
    assert first.completed_at is not None

    updated, _ = await _run(
        db_session,
        {"action": "update", "todo_id": str(second.id), "content": "第二步（改）"},
        session_id=str(session_row.id),
    )
    assert updated.success is True
    await db_session.refresh(second)
    assert second.content == "第二步（改）"

    removed, _ = await _run(
        db_session, {"action": "remove", "todo_id": str(second.id)}, session_id=str(session_row.id)
    )
    assert removed.success is True
    assert removed.data["count"] == 1


async def test_physically_separated_from_node_todos(
    db_session: AsyncSession, task_with_session
) -> None:
    """UT-M5d-2（D5.5 硬要求）：中枢待办写入后，节点上报的 `tasks.todos` 不变。"""
    task, session_row = task_with_session

    await _run(
        db_session, {"action": "add", "items": ["中枢的事"]}, session_id=str(session_row.id)
    )

    await db_session.refresh(task)
    assert task.todos == [{"content": "节点上报的待办", "status": "pending"}]


async def test_without_task_attaches_to_session(
    db_session: AsyncSession,
) -> None:
    """UT-067-1（ADR-067 待办先行）：无任务但有会话 → 挂会话成功（session_id 维度）。"""
    session_row = TaskBuildingSession(
        id=uuid.uuid4(), title="无任务会话", mode="thinking"
    )
    db_session.add(session_row)
    await db_session.commit()

    try:
        result, _ = await _run(
            db_session,
            {"action": "add", "items": ["生成三件套", "创建任务"]},
            session_id=str(session_row.id),
        )
        assert result.success is True
        assert result.data["task_id"] is None
        assert result.data["session_id"] == str(session_row.id)

        rows = (
            await db_session.execute(
                select(TaskOrchestratorTodo).where(
                    TaskOrchestratorTodo.session_id == session_row.id
                )
            )
        ).scalars().all()
        assert [row.content for row in rows] == ["生成三件套", "创建任务"]
        assert all(row.task_id is None for row in rows)
    finally:
        await db_session.execute(
            delete(TaskOrchestratorTodo).where(
                TaskOrchestratorTodo.session_id == session_row.id
            )
        )
        await db_session.execute(
            delete(TaskBuildingSession).where(TaskBuildingSession.id == session_row.id)
        )
        await db_session.commit()


async def test_without_session_and_task_fails_and_writes_nothing(
    db_session: AsyncSession,
) -> None:
    """UT-M5d-3（修订）：无会话且无任务 → 明确失败，且**不写库**。"""
    before = await db_session.scalar(select(TaskOrchestratorTodo.id).limit(1))

    result, _ = await _run(db_session, {"action": "add", "items": ["无主待办"]})

    assert result.success is False
    assert "尚未创建" in result.summary
    after = await db_session.scalar(select(TaskOrchestratorTodo.id).limit(1))
    assert after == before


async def test_content_and_count_limits(
    db_session: AsyncSession, task_with_session
) -> None:
    """UT-M5d-4：空白/超长内容报错；单任务条数上限报错（不静默截断）。"""
    _task, session_row = task_with_session

    blank, _ = await _run(
        db_session, {"action": "add", "items": ["   "]}, session_id=str(session_row.id)
    )
    assert blank.success is False and "不能为空" in blank.summary

    too_long, _ = await _run(
        db_session,
        {"action": "add", "items": ["x" * (ORCHESTRATOR_TODO_CONTENT_MAX + 1)]},
        session_id=str(session_row.id),
    )
    assert too_long.success is False and "过长" in too_long.summary

    ok, _ = await _run(
        db_session,
        {"action": "add", "items": [f"项 {i}" for i in range(ORCHESTRATOR_TODO_MAX_ITEMS)]},
        session_id=str(session_row.id),
    )
    assert ok.success is True

    overflow, _ = await _run(
        db_session, {"action": "add", "items": ["再来一条"]}, session_id=str(session_row.id)
    )
    assert overflow.success is False and "超限" in overflow.summary


async def test_unknown_action_and_missing_todo_id(
    db_session: AsyncSession, task_with_session
) -> None:
    """UT-M5d-4b：非法 action / 缺 todo_id / 跨任务 todo_id 均明确失败。"""
    _task, session_row = task_with_session

    bad_action, _ = await _run(
        db_session, {"action": "explode"}, session_id=str(session_row.id)
    )
    assert bad_action.success is False and "action 非法" in bad_action.summary

    missing_id, _ = await _run(
        db_session, {"action": "complete"}, session_id=str(session_row.id)
    )
    assert missing_id.success is False and "todo_id" in missing_id.summary

    other_task_id, _ = await _run(
        db_session,
        {"action": "complete", "todo_id": str(uuid.uuid4())},
        session_id=str(session_row.id),
    )
    assert other_task_id.success is False and "不存在" in other_task_id.summary


async def test_falls_back_to_parent_task_id(db_session: AsyncSession, task_with_session) -> None:
    """UT-M5d-3b：无构建会话时可用 `parent_task_id` 兜底定位任务。"""
    task, _session_row = task_with_session

    result, _ = await _run(
        db_session, {"action": "add", "items": ["经 parent_task_id 写入"]}, parent_task_id=str(task.id)
    )

    assert result.success is True
    assert result.data["task_id"] == str(task.id)


async def test_status_must_be_known_value(
    db_session: AsyncSession, task_with_session
) -> None:
    """UT-M5d-4c：状态字典外的值由服务层拒绝（技能未暴露该入口，直接测服务）。"""
    from app.services import orchestrator_todo_service as service

    task, _session_row = task_with_session
    rows = await service.add_todos(db_session, task.id, ["一条"])
    with pytest.raises(service.OrchestratorTodoError):
        await service.update_todo(db_session, task.id, rows[0].id, status="done")


async def test_reset_replaces_entire_list(
    db_session: AsyncSession, task_with_session
) -> None:
    """UT-067-2（ADR-067 修订）：reset 清空旧清单重建，替代反复 add 叠加。"""
    task, session_row = task_with_session

    await _run(
        db_session,
        {"action": "add", "items": ["旧计划 A", "旧计划 B"]},
        session_id=str(session_row.id),
    )
    result, _ = await _run(
        db_session,
        {"action": "reset", "items": ["新计划 1", "新计划 2", "新计划 3"]},
        session_id=str(session_row.id),
    )

    assert result.success is True
    assert result.data["count"] == 3
    rows = (
        await db_session.execute(
            select(TaskOrchestratorTodo)
            .where(TaskOrchestratorTodo.task_id == task.id)
            .order_by(TaskOrchestratorTodo.seq)
        )
    ).scalars().all()
    assert [row.content for row in rows] == ["新计划 1", "新计划 2", "新计划 3"]
    assert [row.seq for row in rows] == [1, 2, 3]
    assert {row.status for row in rows} == {"pending"}


async def test_reset_without_items_fails(db_session: AsyncSession, task_with_session) -> None:
    """UT-067-2b：reset 缺 items 明确失败，旧清单保留。"""
    task, session_row = task_with_session

    await _run(
        db_session, {"action": "add", "items": ["保留项"]}, session_id=str(session_row.id)
    )
    result, _ = await _run(db_session, {"action": "reset"}, session_id=str(session_row.id))

    assert result.success is False
    rows = (
        await db_session.execute(
            select(TaskOrchestratorTodo).where(TaskOrchestratorTodo.task_id == task.id)
        )
    ).scalars().all()
    assert [row.content for row in rows] == ["保留项"]


async def test_reset_in_progress_for_chat_session(db_session: AsyncSession) -> None:
    """UT-067-3（ADR-067 修订）：中断/取消后 in_progress 重置 pending——
    会话绑任务时按任务维度、未绑任务时按会话维度。"""
    from app.services import orchestrator_todo_service as service

    # 场景 1：未绑任务（挂会话）
    session_row = TaskBuildingSession(id=uuid.uuid4(), title="中断重置-会话", mode="fast")
    db_session.add(session_row)
    await db_session.commit()
    try:
        added = await service.add_todos(
            db_session, session_id=session_row.id, contents=["进行中项", "未开始项"]
        )
        await service.update_todo(
            db_session, session_id=session_row.id, todo_id=added[0].id, status="in_progress"
        )
        reset = await service.reset_in_progress_for_chat_session(db_session, session_row.id)
        assert reset == 1
        await db_session.refresh(added[0])
        assert added[0].status == "pending"
    finally:
        await db_session.execute(
            delete(TaskOrchestratorTodo).where(
                TaskOrchestratorTodo.session_id == session_row.id
            )
        )
        await db_session.execute(
            delete(TaskBuildingSession).where(TaskBuildingSession.id == session_row.id)
        )
        await db_session.commit()

    # 场景 2：会话绑任务（挂任务维度）
    task = Task(
        id=uuid.uuid4(),
        title="中断重置-任务",
        agent_type="programming",
        status=TaskStatus.PENDING.value,
    )
    bound = TaskBuildingSession(
        id=uuid.uuid4(), title="中断重置-绑任务会话", task_id=task.id, mode="fast"
    )
    db_session.add_all([task, bound])
    await db_session.commit()
    try:
        added = await service.add_todos(db_session, task.id, ["执行中步骤"])
        await service.update_todo(
            db_session, task.id, added[0].id, status="in_progress"
        )
        reset = await service.reset_in_progress_for_chat_session(db_session, bound.id)
        assert reset == 1
        await db_session.refresh(added[0])
        assert added[0].status == "pending"
    finally:
        await db_session.execute(
            delete(TaskOrchestratorTodo).where(TaskOrchestratorTodo.task_id == task.id)
        )
        await db_session.execute(
            delete(TaskBuildingSession).where(TaskBuildingSession.id == bound.id)
        )
        await db_session.execute(delete(Task).where(Task.id == task.id))
        await db_session.commit()
