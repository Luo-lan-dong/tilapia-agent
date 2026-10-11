"""TaskService 会话内自动挂链与可读目录命名测试（ADR-036）。"""

from __future__ import annotations

import itertools
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dispatch_decision import DispatchDecision
from app.models.system_settings import SystemSettings
from app.models.task import Task
from app.models.task_building_session import TaskBuildingSession
from app.schemas.task import TaskCreateRequest, TaskResponse, TaskSpecification
from app.services.task_service import TaskService, _sanitize_title_for_dir

_SPEC = TaskSpecification(
    spec="# 任务规范\n\n开发静态登录页面。",
    tasks="# 任务清单\n\n1. 编写 login.html",
    checklist="# 验收清单\n\n- 页面可打开",
)


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """清理任务、会话、审计与设置表（外键依赖顺序）。"""
    await db_session.execute(TaskBuildingSession.__table__.update().values(task_id=None))
    await db_session.execute(delete(TaskBuildingSession))
    await db_session.execute(delete(Task))
    await db_session.execute(delete(DispatchDecision))
    await db_session.execute(delete(SystemSettings))
    yield


def _request(requirement: str, **kwargs: Any) -> TaskCreateRequest:
    """构造带实质三件套的创建请求（走 spec_chat 直存链路）。"""
    return TaskCreateRequest(requirement=requirement, specification=_SPEC, **kwargs)


_CREATE_SEQ = itertools.count(1)


async def _create(
    service: TaskService, db_session: AsyncSession, request: TaskCreateRequest
) -> TaskResponse:
    """创建任务并显式错开 created_at。

    测试库 db_session 为单事务 savepoint 模式，PostgreSQL ``now()`` 冻结在
    事务起点——同测试内所有任务 created_at 完全相同，"最近任务"排序失义；
    此处按全局递增序号显式写入递增时间戳保证语义可断言。
    """
    response = await service.create_task(db_session, request)
    await db_session.execute(
        sa_update(Task)
        .where(Task.id == response.id)
        .values(created_at=datetime.now(UTC) + timedelta(milliseconds=next(_CREATE_SEQ)))
    )
    await db_session.commit()
    return response


async def _get_task(db_session: AsyncSession, response: TaskResponse) -> Task:
    """按响应 ID 回查 Task 模型（TaskResponse 不含链字段与 output_dir）。"""
    task = await db_session.get(Task, response.id)
    assert task is not None
    return task


async def _new_chat_session(db_session: AsyncSession) -> TaskBuildingSession:
    chat_session = TaskBuildingSession(title="s", messages=[])
    db_session.add(chat_session)
    await db_session.commit()
    await db_session.refresh(chat_session)
    return chat_session


async def test_auto_link_second_task_in_session(db_session: AsyncSession) -> None:
    """同会话第二次创建：自动挂接最近任务为父，root 指向链根，change_brief 自动填充。"""
    chat_session = await _new_chat_session(db_session)
    service = TaskService()
    first_resp = await _create(
        service, db_session, _request("开发静态登录页面", session_id=chat_session.id)
    )
    second_resp = await _create(
        service, db_session, _request("背景改为纯蓝色", session_id=chat_session.id)
    )
    first = await _get_task(db_session, first_resp)
    second = await _get_task(db_session, second_resp)

    assert first.parent_task_id is None
    assert first.root_task_id is None
    assert second.parent_task_id == first.id
    assert second.root_task_id == first.id
    assert second.change_brief == "背景改为纯蓝色"


async def test_auto_link_chains_to_tail(db_session: AsyncSession) -> None:
    """第三次创建挂接到链尾任务（第二任务），root 仍指向链根。"""
    chat_session = await _new_chat_session(db_session)
    service = TaskService()
    first_resp = await _create(
        service, db_session, _request("开发静态登录页面", session_id=chat_session.id)
    )
    second_resp = await _create(
        service, db_session, _request("背景改为蓝色", session_id=chat_session.id)
    )
    third_resp = await _create(
        service, db_session, _request("背景改为纯蓝色", session_id=chat_session.id)
    )
    third = await _get_task(db_session, third_resp)
    second = await _get_task(db_session, second_resp)
    first = await _get_task(db_session, first_resp)

    assert third.parent_task_id == second.id
    assert third.root_task_id == first.id


async def test_explicit_parent_wins(db_session: AsyncSession) -> None:
    """显式 parent_task_id（?parent= 入口）优先，不触发自动挂链与 change_brief 填充。"""
    chat_session = await _new_chat_session(db_session)
    service = TaskService()
    first_resp = await _create(
        service, db_session, _request("开发静态登录页面", session_id=chat_session.id)
    )
    await _create(service, db_session, _request("背景改为蓝色", session_id=chat_session.id))
    third_resp = await _create(
        service,
        db_session,
        _request("重新设计首页", session_id=chat_session.id, parent_task_id=first_resp.id),
    )
    third = await _get_task(db_session, third_resp)
    first = await _get_task(db_session, first_resp)

    assert third.parent_task_id == first.id
    assert third.root_task_id == first.id
    assert third.change_brief is None


async def test_auto_change_brief_not_overwritten(db_session: AsyncSession) -> None:
    """请求已携带 change_brief 时保留原值。"""
    chat_session = await _new_chat_session(db_session)
    service = TaskService()
    await _create(service, db_session, _request("开发静态登录页面", session_id=chat_session.id))
    second_resp = await _create(
        service,
        db_session,
        _request("背景改纯蓝", session_id=chat_session.id, change_brief="自定义说明"),
    )
    second = await _get_task(db_session, second_resp)

    assert second.change_brief == "自定义说明"


async def test_root_dir_named_with_title_and_id_prefix(db_session: AsyncSession) -> None:
    """新链根目录名为 `净化标题-任务ID前8位`，不含文件系统非法字符。"""
    service = TaskService()
    resp = await service.create_task(db_session, _request("静态登录页面: 背景改造 纯蓝/白?"))
    task = await _get_task(db_session, resp)

    assert task.output_dir is not None
    expected = f"{_sanitize_title_for_dir('静态登录页面: 背景改造 纯蓝/白?')}-{str(task.id)[:8]}"
    assert task.output_dir == expected
    for illegal in ("/", "\\", ":", "*", "?", '"', "<", ">", "|"):
        assert illegal not in task.output_dir


def test_sanitize_title_rules() -> None:
    """净化规则：非法字符与空白转 -、压缩连字符、截断、可退化为空串。"""
    assert _sanitize_title_for_dir('a/b\\c:d*e?f"g<h>i|j') == "a-b-c-d-e-f-g-h-i-j"
    assert _sanitize_title_for_dir("  多  空格 --测试  ") == "多-空格-测试"
    assert len(_sanitize_title_for_dir("超长标题" * 20)) <= 20
    assert _sanitize_title_for_dir("///") == ""


async def test_child_inherits_root_output_dir(db_session: AsyncSession) -> None:
    """链上子任务继承链根任务的 output_dir（同链同目录）。"""
    chat_session = await _new_chat_session(db_session)
    service = TaskService()
    first_resp = await _create(
        service, db_session, _request("开发静态登录页面", session_id=chat_session.id)
    )
    second_resp = await _create(
        service, db_session, _request("背景改纯蓝", session_id=chat_session.id)
    )
    first = await _get_task(db_session, first_resp)
    second = await _get_task(db_session, second_resp)

    assert first.output_dir is not None
    assert second.output_dir == first.output_dir


async def test_child_falls_back_when_root_dir_empty(db_session: AsyncSession) -> None:
    """链根 output_dir 为空（历史数据）时子任务留空，Agent 端按 root_id 回退。"""
    chat_session = await _new_chat_session(db_session)
    service = TaskService()
    first_resp = await _create(
        service, db_session, _request("开发静态登录页面", session_id=chat_session.id)
    )
    first = await _get_task(db_session, first_resp)
    first.output_dir = None
    await db_session.commit()
    second_resp = await _create(
        service, db_session, _request("背景改纯蓝", session_id=chat_session.id)
    )
    second = await _get_task(db_session, second_resp)

    assert second.output_dir is None


async def test_explicit_output_dir_priority(db_session: AsyncSession) -> None:
    """用户显式 output_dir 优先级最高，链上子任务继承显式目录。"""
    chat_session = await _new_chat_session(db_session)
    service = TaskService()
    first_resp = await _create(
        service,
        db_session,
        _request("开发静态登录页面", session_id=chat_session.id, output_dir="my-project"),
    )
    second_resp = await _create(
        service, db_session, _request("背景改纯蓝", session_id=chat_session.id)
    )
    third_resp = await _create(
        service, db_session, _request("另一条新链任务", output_dir="other/dir")
    )
    first = await _get_task(db_session, first_resp)
    second = await _get_task(db_session, second_resp)
    third = await _get_task(db_session, third_resp)

    assert first.output_dir == "my-project"
    assert second.output_dir == "my-project"
    assert third.output_dir == "other/dir"


async def test_no_session_still_auto_named(db_session: AsyncSession) -> None:
    """无 session_id（API 直接创建）：不挂链，目录仍自动可读命名。"""
    service = TaskService()
    resp = await service.create_task(db_session, _request("开发静态登录页面"))
    task = await _get_task(db_session, resp)

    assert task.parent_task_id is None
    assert task.root_task_id is None
    assert task.output_dir is not None
    assert task.output_dir.endswith(f"-{str(task.id)[:8]}")


async def test_auto_link_ignores_other_sessions(db_session: AsyncSession) -> None:
    """其他会话创建的任务不参与本会话自动挂链。"""
    session_a = await _new_chat_session(db_session)
    session_b = await _new_chat_session(db_session)
    service = TaskService()
    await _create(service, db_session, _request("A 会话任务", session_id=session_a.id))
    b_resp = await _create(service, db_session, _request("B 会话任务", session_id=session_b.id))
    b_task = await _get_task(db_session, b_resp)

    assert b_task.parent_task_id is None
    tasks = list((await db_session.execute(select(Task))).scalars().all())
    assert len(tasks) == 2
