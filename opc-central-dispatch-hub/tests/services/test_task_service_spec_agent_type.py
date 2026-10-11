"""spec_chat 任务创建的 agent_type 判定测试（ADR-062 回归）。

背景：`_create_task_from_spec` 曾硬编码 ``agent_type="code"``，导致新建对话
创建的文档任务被调度到编程节点（office 节点闲置）。修复后按需求用规划 Agent
判定，失败回退 code。
"""

from unittest.mock import AsyncMock, patch

from app.agents.planning import TaskPlanResult
from app.schemas.task import TaskCreateRequest
from app.schemas.task_building import TaskSpecification
from app.services.task_service import TaskService

_SUBSTANTIVE_SPEC = TaskSpecification(
    spec="# 任务规范\n\n写一份团队周报写作规范。",
    tasks="# 任务清单\n\n1. 起草章节",
    checklist="# 验收清单\n\n- [ ] 含表格示例",
)


def _request(requirement: str) -> TaskCreateRequest:
    return TaskCreateRequest(
        requirement=requirement,
        specification=_SUBSTANTIVE_SPEC,
    )


async def test_spec_chat_resolves_office_agent_type(db_session) -> None:
    """办公类需求创建的 spec_chat 任务应为 office 类型（ADR-062）。"""
    fake_plan = TaskPlanResult(
        task_name="团队周报写作规范",
        description="写一份团队周报写作规范 docx",
        agent_type="office",
    )
    with patch(
        "app.services.task_service.TaskPlanningAgent.plan",
        new=AsyncMock(return_value=fake_plan),
    ):
        response = await TaskService().create_task(db_session, _request("写一份团队周报写作规范文档"))

    assert response.agent_type == "office"
    assert response.status == "pending"


async def test_spec_chat_falls_back_to_code_on_planning_failure(db_session) -> None:
    """规划失败时回退 code，任务创建不被阻塞。"""
    with patch(
        "app.services.task_service.TaskPlanningAgent.plan",
        new=AsyncMock(side_effect=RuntimeError("deepseek down")),
    ):
        response = await TaskService().create_task(db_session, _request("写一份文档"))

    assert response.agent_type == "code"
    assert response.status == "pending"
