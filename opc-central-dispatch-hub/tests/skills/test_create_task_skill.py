"""CreateTaskSkill 测试（ADR-033）：三件套创建任务 + 会话绑定 + 规则包绑定（ADR-048）。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_node import AgentNode
from app.models.dispatch_decision import DispatchDecision
from app.models.standard_doc import StandardDoc
from app.models.system_settings import SystemSettings
from app.models.task import Task
from app.models.task_building_session import TaskBuildingSession
from app.models.task_orchestrator_todo import TaskOrchestratorTodo
from app.skills.base import SkillContext
from app.skills.task_creation import CreateTaskSkill

_SUBSTANTIVE_SPEC = {
    "spec": "# 任务规范\n\n开发一个静态登录页面，包含邮箱密码输入框与登录按钮。",
    "tasks": "# 任务清单\n\n1. 编写 login.html\n2. 添加样式\n3. 交互脚本",
    "checklist": "# 验收清单\n\n- 页面可打开\n- 表单可输入",
}


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """清理任务、节点、会话、审计与设置表（外键依赖顺序；保留预置模板）。"""
    await db_session.execute(delete(DispatchDecision))
    await db_session.execute(TaskBuildingSession.__table__.update().values(task_id=None))
    await db_session.execute(delete(TaskBuildingSession))
    await db_session.execute(delete(Task))
    await db_session.execute(delete(AgentNode))
    await db_session.execute(delete(SystemSettings))
    await db_session.execute(delete(StandardDoc).where(StandardDoc.is_builtin.is_(False)))
    yield


async def test_create_task_from_session_specification(db_session: AsyncSession) -> None:
    """三件套非空时创建任务成功，任务 pending 且自动关联会话。"""
    chat_session = TaskBuildingSession(title="s", messages=[])
    db_session.add(chat_session)
    await db_session.commit()
    await db_session.refresh(chat_session)

    skill = CreateTaskSkill()
    context = SkillContext(
        args={},
        messages=[{"role": "user", "content": "我想用HTML写一个登录页面"}],
        current_specification=_SUBSTANTIVE_SPEC,
        session_id=str(chat_session.id),
    )
    events = [event async for event in skill.execute(db_session, context)]

    result = next(event["data"] for event in events if event["event"] == "_result")
    assert result.success is True
    task_id = result.data["task_id"]

    created = await db_session.get(Task, uuid.UUID(task_id))
    assert created is not None
    assert created.status == "pending"
    assert created.source == "spec_chat"
    assert created.agent_type == "code"

    refreshed_session = await db_session.get(TaskBuildingSession, chat_session.id)
    assert refreshed_session is not None
    assert str(refreshed_session.task_id) == task_id


async def test_create_task_binds_rule_docs_by_name(db_session: AsyncSession) -> None:
    """rule_doc_names 按名解析绑定：存在的锁定快照，不存在的跳过并在结果中提示。"""
    doc = StandardDoc(
        name="test-py-standards",
        category="coding-standard",
        apply_when="写/改后端代码前",
        content="# 后端规范",
        is_builtin=False,
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    skill = CreateTaskSkill()
    context = SkillContext(
        args={"rule_doc_names": ["test-py-standards", "not-exist-doc"]},
        messages=[{"role": "user", "content": "开发一个 FastAPI 待办事项应用"}],
        current_specification=_SUBSTANTIVE_SPEC,
    )
    events = [event async for event in skill.execute(db_session, context)]

    result = next(event["data"] for event in events if event["event"] == "_result")
    assert result.success is True
    assert result.data["rule_docs"] == ["test-py-standards"]
    assert result.data["rule_docs_missing"] == ["not-exist-doc"]

    created = await db_session.get(Task, uuid.UUID(result.data["task_id"]))
    assert created is not None
    assert created.rule_docs is not None and len(created.rule_docs) == 1
    snapshot = created.rule_docs[0]
    assert snapshot["name"] == "test-py-standards"
    assert snapshot["content"] == "# 后端规范"


async def test_create_task_without_rule_docs_keeps_null(db_session: AsyncSession) -> None:
    """未传 rule_doc_names 时不绑定规则包（节点自由发挥的回归语义）。"""
    skill = CreateTaskSkill()
    context = SkillContext(
        args={},
        messages=[{"role": "user", "content": "开发一个静态页面"}],
        current_specification=_SUBSTANTIVE_SPEC,
    )
    events = [event async for event in skill.execute(db_session, context)]

    result = next(event["data"] for event in events if event["event"] == "_result")
    assert result.success is True
    created = await db_session.get(Task, uuid.UUID(result.data["task_id"]))
    assert created is not None
    assert created.rule_docs is None


async def test_create_task_passes_environment_requirements(
    db_session: AsyncSession,
) -> None:
    """environment 参数透传到任务行（ADR-048 P2）：子节点 AI 首次开发时检查并安装。"""
    env = [{"name": "Python", "check_cmd": "python3 --version", "min_version": "3.10"}]
    skill = CreateTaskSkill()
    context = SkillContext(
        args={"environment": env},
        messages=[{"role": "user", "content": "开发一个 FastAPI 应用"}],
        current_specification=_SUBSTANTIVE_SPEC,
    )
    events = [event async for event in skill.execute(db_session, context)]

    result = next(event["data"] for event in events if event["event"] == "_result")
    assert result.success is True
    created = await db_session.get(Task, uuid.UUID(result.data["task_id"]))
    assert created is not None
    assert created.environment_requirements == env


async def test_create_task_without_specification_fails(db_session: AsyncSession) -> None:
    """三件套缺失或仍是空模板时失败，提示先生成文档。"""
    skill = CreateTaskSkill()
    context = SkillContext(args={}, messages=[], current_specification=None)
    events = [event async for event in skill.execute(db_session, context)]

    result = next(event["data"] for event in events if event["event"] == "_result")
    assert result.success is False
    assert "三件套" in result.summary

    tasks = list((await db_session.execute(select(Task))).scalars().all())
    assert tasks == []


async def test_create_task_refused_when_spec_generation_failed(db_session: AsyncSession) -> None:
    """本轮三件套生成失败时必须拒绝创建——即使会话仍有实质性旧草稿，
    防止基于过时规范误建任务、派发必然失败（修复：生成失败仍创建任务）。"""
    skill = CreateTaskSkill()
    context = SkillContext(
        args={},
        messages=[{"role": "user", "content": "我想用HTML写一个登录页面"}],
        current_specification=_SUBSTANTIVE_SPEC,
        spec_generation_failed=True,
    )
    events = [event async for event in skill.execute(db_session, context)]

    result = next(event["data"] for event in events if event["event"] == "_result")
    assert result.success is False
    assert "生成失败" in result.summary

    tasks = list((await db_session.execute(select(Task))).scalars().all())
    assert tasks == []


async def test_skill_passes_change_brief(db_session: AsyncSession) -> None:
    """技能把 requirement 作为本轮修改说明（change_brief）透传落库（ADR-036）。"""
    chat_session = TaskBuildingSession(title="s", messages=[])
    db_session.add(chat_session)
    await db_session.commit()
    await db_session.refresh(chat_session)

    skill = CreateTaskSkill()
    context = SkillContext(
        args={"requirement": "背景改为纯蓝色"},
        messages=[{"role": "user", "content": "把背景改成纯蓝色"}],
        current_specification=_SUBSTANTIVE_SPEC,
        session_id=str(chat_session.id),
    )
    events = [event async for event in skill.execute(db_session, context)]

    result = next(event["data"] for event in events if event["event"] == "_result")
    assert result.success is True
    created = await db_session.get(Task, uuid.UUID(result.data["task_id"]))
    assert created is not None
    assert created.change_brief == "背景改为纯蓝色"


async def test_skill_auto_link_via_service(db_session: AsyncSession) -> None:
    """同会话内第二次调用技能：服务层自动挂链到第一次的任务（ADR-036）。"""
    chat_session = TaskBuildingSession(title="s", messages=[])
    db_session.add(chat_session)
    await db_session.commit()
    await db_session.refresh(chat_session)

    skill = CreateTaskSkill()
    base = {
        "messages": [{"role": "user", "content": "我想用HTML写一个登录页面"}],
        "current_specification": _SUBSTANTIVE_SPEC,
        "session_id": str(chat_session.id),
    }
    first_events = [
        event async for event in skill.execute(db_session, SkillContext(args={}, **base))
    ]
    first_id = next(event["data"] for event in first_events if event["event"] == "_result").data[
        "task_id"
    ]

    second_events = [
        event
        async for event in skill.execute(
            db_session,
            SkillContext(args={"requirement": "背景改为纯蓝色"}, **base),
        )
    ]
    second_result = next(event["data"] for event in second_events if event["event"] == "_result")
    assert second_result.success is True

    second = await db_session.get(Task, uuid.UUID(second_result.data["task_id"]))
    first = await db_session.get(Task, uuid.UUID(first_id))
    assert second is not None and first is not None
    assert second.parent_task_id == first.id
    assert second.root_task_id == first.id
    # 链上子任务继承链根目录
    assert second.output_dir == first.output_dir


async def test_create_task_sets_dispatch_hold_and_migrates_session_todos(
    db_session: AsyncSession,
) -> None:
    """ADR-067：create_task 置 dispatch_hold（调度器跳过）+ 会话待办迁移归属新任务。"""
    from app.services.orchestrator_todo_service import add_todos

    chat_session = TaskBuildingSession(title="s", messages=[])
    db_session.add(chat_session)
    await db_session.commit()
    await db_session.refresh(chat_session)
    # 会话名下预置待办（待办先行场景：任务尚未创建时挂会话）
    await add_todos(
        db_session,
        None,
        ["生成三件套规范文档", "创建任务", "向用户报告并等待确认派发"],
        session_id=chat_session.id,
    )

    skill = CreateTaskSkill()
    context = SkillContext(
        args={"requirement": "开发简易登录系统"},
        messages=[{"role": "user", "content": "开发简易登录系统"}],
        current_specification=_SUBSTANTIVE_SPEC,
        session_id=str(chat_session.id),
    )
    events = [event async for event in skill.execute(db_session, context)]

    result = next(event["data"] for event in events if event["event"] == "_result")
    assert result.success is True

    created = await db_session.get(Task, uuid.UUID(result.data["task_id"]))
    assert created is not None
    # 派发确认门禁：调度器跳过，等待用户确认
    assert created.dispatch_hold is True

    # 会话待办全部迁移归属到新任务
    migrated = (
        await db_session.execute(
            select(TaskOrchestratorTodo)
            .where(TaskOrchestratorTodo.task_id == created.id)
            .order_by(TaskOrchestratorTodo.seq)
        )
    ).scalars().all()
    assert [row.content for row in migrated] == [
        "生成三件套规范文档",
        "创建任务",
        "向用户报告并等待确认派发",
    ]
    assert all(row.session_id is None for row in migrated)
    # 会话名下不再有无主待办
    leftover = (
        await db_session.execute(
            select(TaskOrchestratorTodo).where(
                TaskOrchestratorTodo.session_id == chat_session.id,
                TaskOrchestratorTodo.task_id.is_(None),
            )
        )
    ).scalars().all()
    assert leftover == []
    # 结果摘要提示已迁移
    assert "迁移" in result.summary
