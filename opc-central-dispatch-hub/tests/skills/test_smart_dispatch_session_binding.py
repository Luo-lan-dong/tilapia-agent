"""SmartDispatchSkill 会话绑定测试（ADR-033）：派发成功回写会话 task_id。"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_node import AgentNode, NodeStatus
from app.models.dispatch_decision import DispatchDecision
from app.models.system_settings import SystemSettings
from app.models.task import Task, TaskStatus
from app.models.task_building_session import TaskBuildingSession
from app.skills.base import SkillContext
from app.skills.dispatch_skill import SmartDispatchSkill


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """清理任务、节点、会话、审计与设置表（外键依赖顺序）。"""
    await db_session.execute(delete(DispatchDecision))
    await db_session.execute(
        TaskBuildingSession.__table__.update().values(task_id=None)
    )
    await db_session.execute(delete(TaskBuildingSession))
    await db_session.execute(delete(Task))
    await db_session.execute(delete(AgentNode))
    await db_session.execute(delete(SystemSettings))
    yield


@pytest.fixture(autouse=True)
def _ai_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量兜底值固定为关闭，隔离宿主 .env 影响。"""
    monkeypatch.setenv("AI_DISPATCH_ENABLED", "false")


async def test_smart_dispatch_binds_task_to_session(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """派发成功后任务 ID 回写会话，前端据此进入跟踪态展示产物。"""

    async def _fake_llm(session: AsyncSession, user_prompt: str) -> str:
        # 从 prompt 中取唯一候选节点 ID（白名单决策）
        import re

        match = re.search(r'"node_id": "([0-9a-f-]+)"', user_prompt)
        node_id = match.group(1) if match else str(uuid.uuid4())
        task_match = re.search(r"task_id=([0-9a-f-]+)", user_prompt)
        task_id = task_match.group(1) if task_match else str(uuid.uuid4())
        return json.dumps(
            {"decisions": [{"task_id": task_id, "node_id": node_id, "reason": "唯一候选"}]}
        )

    monkeypatch.setattr("app.services.dispatch_decision_service._call_llm_json", _fake_llm)

    node = AgentNode(
        name=f"node-{uuid.uuid4().hex[:8]}",
        agent_type="code",
        status=NodeStatus.IDLE.value,
        load=1.0,
        registration_token=f"token-{uuid.uuid4().hex}",
    )
    chat_session = TaskBuildingSession(title="s", messages=[])
    task = Task(
        title="t",
        agent_type="code",
        status=TaskStatus.PENDING.value,
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
    )
    db_session.add_all([node, chat_session, task])
    await db_session.commit()
    for obj in (node, chat_session, task):
        await db_session.refresh(obj)

    skill = SmartDispatchSkill()
    context = SkillContext(
        args={"task_id": str(task.id)},
        session_id=str(chat_session.id),
    )
    events = [event async for event in skill.execute(db_session, context)]

    result = next(event["data"] for event in events if event["event"] == "_result")
    assert result.success is True

    refreshed_task = await db_session.get(Task, task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == TaskStatus.ASSIGNED.value

    refreshed_session = await db_session.get(TaskBuildingSession, chat_session.id)
    assert refreshed_session is not None
    assert refreshed_session.task_id == task.id


async def test_smart_dispatch_without_session_id_skips_binding(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无会话上下文（session_id 缺省）时跳过绑定，派发照常成功。"""

    async def _fake_llm(session: AsyncSession, user_prompt: str) -> str:
        import re

        match = re.search(r'"node_id": "([0-9a-f-]+)"', user_prompt)
        node_id = match.group(1) if match else str(uuid.uuid4())
        task_match = re.search(r"task_id=([0-9a-f-]+)", user_prompt)
        task_id = task_match.group(1) if task_match else str(uuid.uuid4())
        return json.dumps({"decisions": [{"task_id": task_id, "node_id": node_id, "reason": "ok"}]})

    monkeypatch.setattr("app.services.dispatch_decision_service._call_llm_json", _fake_llm)

    node = AgentNode(
        name=f"node-{uuid.uuid4().hex[:8]}",
        agent_type="code",
        status=NodeStatus.IDLE.value,
        load=0.5,
        registration_token=f"token-{uuid.uuid4().hex}",
    )
    task = Task(
        title="t2",
        agent_type="code",
        status=TaskStatus.PENDING.value,
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
    )
    db_session.add_all([node, task])
    await db_session.commit()
    await db_session.refresh(task)

    skill = SmartDispatchSkill()
    context = SkillContext(args={"task_id": str(task.id)}, session_id=None)
    events = [event async for event in skill.execute(db_session, context)]

    result = next(event["data"] for event in events if event["event"] == "_result")
    assert result.success is True
    sessions = list((await db_session.execute(select(TaskBuildingSession))).scalars().all())
    assert sessions == []
