"""环境要求清单下发测试（ADR-048 P2）。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_node import AgentNode
from app.models.dispatch_decision import DispatchDecision
from app.models.standard_doc import StandardDoc
from app.models.system_settings import SystemSettings
from app.models.task import Task
from app.models.task_building_session import TaskBuildingSession
from app.schemas.task import TaskCreateRequest
from app.schemas.task_building import TaskSpecification
from app.services.scheduler import _build_dispatch_message
from app.services.task_service import TaskService

_ENV = [
    {"name": "Python", "check_cmd": "python3 --version", "min_version": "3.10"},
    {"name": "Node", "check_cmd": "node --version", "install_hint": "nvm install 20"},
]


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """清理相关表（保留预置模板）。"""
    await db_session.execute(delete(DispatchDecision))
    await db_session.execute(TaskBuildingSession.__table__.update().values(task_id=None))
    await db_session.execute(delete(TaskBuildingSession))
    await db_session.execute(delete(Task))
    await db_session.execute(delete(AgentNode))
    await db_session.execute(delete(SystemSettings))
    await db_session.execute(delete(StandardDoc).where(StandardDoc.is_builtin.is_(False)))
    yield


async def test_create_task_persists_environment_requirements(
    db_session: AsyncSession,
) -> None:
    """创建任务带 environment_requirements → 落库（技能与 REST 共用链路）。"""
    request = TaskCreateRequest(
        requirement="环境清单落库验证",
        specification=TaskSpecification(spec="# s", tasks="# t", checklist="# c"),
        environment_requirements=_ENV,
    )
    created = await TaskService().create_task(db_session, request)
    assert created.environment_requirements == _ENV


async def test_create_task_without_env_requirements_keeps_null(
    db_session: AsyncSession,
) -> None:
    """未传环境清单时保持 NULL（回归语义）。"""
    request = TaskCreateRequest(
        requirement="无环境清单",
        specification=TaskSpecification(spec="# s", tasks="# t", checklist="# c"),
    )
    created = await TaskService().create_task(db_session, request)
    assert created.environment_requirements is None


def test_build_dispatch_message_inlines_environment_requirements() -> None:
    """派发 payload 内联 environment_requirements（空清单归一为空列表）。"""
    task = Task(
        title="t",
        agent_type="code",
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
        environment_requirements=_ENV,
    )
    message = _build_dispatch_message(task)
    assert message["payload"]["environment_requirements"] == _ENV

    task_none = Task(
        title="t2",
        agent_type="code",
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
    )
    message_none = _build_dispatch_message(task_none)
    assert message_none["payload"]["environment_requirements"] == []
