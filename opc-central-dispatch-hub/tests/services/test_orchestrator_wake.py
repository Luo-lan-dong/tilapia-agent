"""ADR-066 任务终态自动唤醒编排测试。

覆盖 Harness UT-W1~W5 与 IT-W1~W6 的关键路径：

- 触发消息模板（completed/failed 两态）；
- 限流纯逻辑（同链 24h 次数 / 连续失败次数）；
- 前置检查矩阵（状态/模式/开关/会话关联/限流，db_session savepoint 直传）；
- 后台轮落库（patch AsyncSessionLocal + fake chat_stream）。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker as _sessionmaker

from app.models.system_settings import SystemSettings
from app.models.task import Task
from app.models.task_building_session import TaskBuildingSession
from app.models.tool_call_log import ToolCallLog
from app.services import orchestrator_wake_service as wake_service
from app.services.orchestrator_wake_service import (
    _chain_wake_ok,
    _load_wake_context,
    build_wake_message,
)


async def _make_task(session: AsyncSession, **overrides: Any) -> Task:
    """构造可落库的任务行（completed + build + 关联会话缺省）。"""
    defaults: dict[str, Any] = {
        "title": "测试任务",
        "agent_type": "code",
        "status": "completed",
        "execution_mode": "build",
        "specification": {"spec": "# s", "tasks": "# t", "checklist": "# c"},
    }
    defaults.update(overrides)
    task = Task(**defaults)
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """清理任务/会话/设置/审计表（外键顺序）。"""
    await db_session.execute(delete(ToolCallLog))
    await db_session.execute(TaskBuildingSession.__table__.update().values(task_id=None))
    await db_session.execute(delete(TaskBuildingSession))
    await db_session.execute(delete(Task))
    await db_session.execute(delete(SystemSettings))
    yield


# ---------------------------------------------------------------------------
# UT-W1：触发消息模板
# ---------------------------------------------------------------------------


def test_build_wake_message_completed_contains_directives() -> None:
    """completed 触发消息含状态、回报与四条指令要点。"""
    msg = build_wake_message(
        title="开发登录页", terminal_status="completed", detail="已创建 login.html"
    )
    assert "[系统自动唤醒]" in msg
    assert "开发登录页" in msg and "已完成" in msg
    assert "已创建 login.html" in msg
    assert "todo_write" in msg and "get_task_status" in msg
    assert "未经用户确认不要创建或派发新任务" in msg


def test_build_wake_message_failed_uses_failure_detail() -> None:
    """failed 触发消息使用失败说明，且状态词为执行失败。"""
    msg = build_wake_message(title="t", terminal_status="failed", detail="编译错误")
    assert "执行失败" in msg and "编译错误" in msg


# ---------------------------------------------------------------------------
# UT-W2/W3：限流纯逻辑
# ---------------------------------------------------------------------------


def test_chain_wake_ok_limits() -> None:
    """同链 24h ≤5 次且连续失败 ≤3 次内放行，任一超限拒绝。"""
    assert _chain_wake_ok(chain_wake_count_24h=4, consecutive_failure_wakes=2) is True
    assert _chain_wake_ok(chain_wake_count_24h=4, consecutive_failure_wakes=3) is False
    assert _chain_wake_ok(chain_wake_count_24h=5, consecutive_failure_wakes=0) is False


# ---------------------------------------------------------------------------
# IT：前置检查矩阵（db_session savepoint 直传）
# ---------------------------------------------------------------------------


async def test_load_wake_context_returns_for_completed_with_session(
    db_session: AsyncSession,
) -> None:
    """completed + build + 关联会话 + 开关开 → 返回唤醒上下文。"""
    chat = TaskBuildingSession(title="s", messages=[])
    db_session.add(chat)
    await db_session.commit()
    await db_session.refresh(chat)
    task = await _make_task(db_session, session_id=chat.id)

    context = await _load_wake_context(db_session, str(task.id))
    assert context is not None
    assert context["terminal_status"] == "completed"
    assert context["session_id"] == str(chat.id)
    assert context["root_task_id"] == str(task.id)


async def test_load_wake_context_returns_for_failed(db_session: AsyncSession) -> None:
    """failed 终态同样返回上下文（terminal_status=failed）。"""
    chat = TaskBuildingSession(title="s", messages=[])
    db_session.add(chat)
    await db_session.commit()
    await db_session.refresh(chat)
    task = await _make_task(db_session, session_id=chat.id, status="failed")

    context = await _load_wake_context(db_session, str(task.id))
    assert context is not None and context["terminal_status"] == "failed"


async def test_load_wake_context_skips_non_wake_status(
    db_session: AsyncSession,
) -> None:
    """verify_blocked / review / cancelled / running 不触发。"""
    for status in ("verify_blocked", "review", "requeue_pending", "assigned"):
        task = await _make_task(db_session, status=status)
        assert await _load_wake_context(db_session, str(task.id)) is None, status


async def test_load_wake_context_skips_without_session(
    db_session: AsyncSession,
) -> None:
    """无关联会话（API 直建）不触发。"""
    task = await _make_task(db_session, session_id=None)
    assert await _load_wake_context(db_session, str(task.id)) is None


async def test_load_wake_context_skips_plan_mode(db_session: AsyncSession) -> None:
    """plan 模式不触发（review 流程有自己的审批入口）。"""
    task = await _make_task(db_session, execution_mode="plan")
    assert await _load_wake_context(db_session, str(task.id)) is None


async def test_load_wake_context_skips_when_disabled(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """开关关闭不触发（monkeypatch 开关读取函数）。"""
    chat = TaskBuildingSession(title="s", messages=[])
    db_session.add(chat)
    await db_session.commit()
    await db_session.refresh(chat)
    task = await _make_task(db_session, session_id=chat.id)

    async def _disabled(session: AsyncSession) -> bool:
        return False

    monkeypatch.setattr(wake_service, "is_orchestrator_auto_wake_enabled", _disabled)
    assert await _load_wake_context(db_session, str(task.id)) is None


async def test_load_wake_context_rate_limited_by_chain_count(
    db_session: AsyncSession,
) -> None:
    """同链 24h 唤醒审计达 5 条 → 限流拒绝。"""
    chat = TaskBuildingSession(title="s", messages=[])
    db_session.add(chat)
    await db_session.commit()
    await db_session.refresh(chat)
    task = await _make_task(db_session, session_id=chat.id)
    for _ in range(5):
        db_session.add(
            ToolCallLog(
                kind="orchestrator_wake",
                name="completed",
                status="success",
                task_id=str(task.id),
                result_summary='{"root_task_id": "' + str(task.id) + '"}',
            )
        )
    await db_session.commit()

    assert await _load_wake_context(db_session, str(task.id)) is None


async def test_load_wake_context_rate_limited_by_failure_streak(
    db_session: AsyncSession,
) -> None:
    """同链最近 3 条唤醒审计均为 failed → 限流拒绝。"""
    chat = TaskBuildingSession(title="s", messages=[])
    db_session.add(chat)
    await db_session.commit()
    await db_session.refresh(chat)
    task = await _make_task(db_session, session_id=chat.id, status="failed")
    for _ in range(3):
        db_session.add(
            ToolCallLog(
                kind="orchestrator_wake",
                name="failed",
                status="success",
                task_id=str(task.id),
                result_summary='{"root_task_id": "' + str(task.id) + '"}',
            )
        )
    await db_session.commit()

    assert await _load_wake_context(db_session, str(task.id)) is None


# ---------------------------------------------------------------------------
# IT：后台轮落库（patch AsyncSessionLocal + fake 编排流）
# ---------------------------------------------------------------------------


async def test_execute_wake_round_persists_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """唤醒轮把触发消息与 AI 回复落库到会话 messages。

    使用独立 engine 真实提交（append_message 内部 commit 绕过 savepoint），
    测试结束后清理任务/会话行。
    """
    from app.core.config import get_settings as _gs

    engine = create_async_engine(_gs().database_url, echo=False)
    maker = _sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        # 真实提交建行（外部 engine 可见，唤醒轮的独立会话才能读到）
        async with maker() as setup_session:
            chat = TaskBuildingSession(title="s", messages=[])
            setup_session.add(chat)
            task = Task(
                title="测试任务",
                agent_type="code",
                status="completed",
                execution_mode="build",
                specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
                session_id=chat.id,
            )
            setup_session.add(task)
            await setup_session.commit()
            chat_id = chat.id
            task_id = task.id

        test_maker = _sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        monkeypatch.setattr(wake_service, "AsyncSessionLocal", test_maker)

        async def fake_chat_stream(
            *args: Any, **kwargs: Any
        ) -> AsyncGenerator[dict, None]:
            yield {"event": "meta", "data": {"model": "fake-model", "mode": "thinking"}}
            yield {
                "event": "chunk",
                "data": {"delta": "任务已完成，交付物 login.html 已核对。"},
            }
            yield {"event": "done", "data": {}}

        class FakeService:
            def __init__(self, session: AsyncSession) -> None:
                pass

            def chat_stream(self, *args: Any, **kwargs: Any):
                return fake_chat_stream()

        monkeypatch.setattr(wake_service, "OrchestratorService", FakeService)

        context = {
            "task_id": str(task_id),
            "title": "测试任务",
            "terminal_status": "completed",
            "change_summary": "已创建 login.html",
            "error_hint": None,
            "session_id": str(chat_id),
            "root_task_id": str(task_id),
            "specification": None,
        }
        await wake_service._execute_wake_round(context)

        async with maker() as verify_session:
            refreshed = await verify_session.get(TaskBuildingSession, chat_id)
            assert refreshed is not None
            roles = [m.get("role") for m in refreshed.messages]
            assert "user" in roles and "assistant" in roles
            wake_msg = next(
                (m for m in refreshed.messages if m.get("source") == "system_wake"), None
            )
            assert wake_msg is not None
            assert "[系统自动唤醒]" in (wake_msg.get("content") or "")
            reply = next(m for m in refreshed.messages if m.get("role") == "assistant")
            assert "login.html 已核对" in (reply.get("content") or "")
    finally:
        # 清理真实落库的行（无外键顺序问题：先解除关联再删除）
        async with maker() as cleanup_session:
            await cleanup_session.execute(
                TaskBuildingSession.__table__.update().values(task_id=None)
            )
            await cleanup_session.execute(delete(TaskBuildingSession))
            await cleanup_session.execute(delete(Task))
            await cleanup_session.commit()
        await engine.dispose()


async def test_execute_wake_round_skips_when_steering_busy(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会话被前端流占用且重试耗尽 → 放弃本轮，不落任何消息。"""
    from app.services import steering_service

    chat = TaskBuildingSession(title="s", messages=[])
    db_session.add(chat)
    await db_session.commit()
    await db_session.refresh(chat)
    task = await _make_task(db_session, session_id=chat.id)

    monkeypatch.setattr(wake_service, "_STEERING_RETRY_TIMES", 1)
    monkeypatch.setattr(wake_service, "_STEERING_RETRY_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(steering_service, "is_active", lambda sid: True)

    context = {
        "task_id": str(task.id),
        "title": task.title,
        "terminal_status": "completed",
        "change_summary": None,
        "error_hint": None,
        "session_id": str(chat.id),
        "root_task_id": str(task.id),
        "specification": None,
    }
    await wake_service._execute_wake_round(context)

    refreshed = await db_session.get(TaskBuildingSession, chat.id)
    assert refreshed is not None
    assert refreshed.messages == []
