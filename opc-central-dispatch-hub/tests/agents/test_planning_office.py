"""ADR-062 office 节点中枢侧契约测试（Harness C-1/C-2/C-3）。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.planning import (
    VALID_AGENT_TYPES,
    TaskPlanningAgent,
    TaskPlanResult,
)


@pytest.fixture
def mock_deepseek_client() -> MagicMock:
    """提供模拟的 DeepSeekClient 实例。"""
    client = MagicMock()
    client.chat_completion = AsyncMock()
    return client


@pytest.fixture
def planning_agent(mock_deepseek_client: MagicMock) -> TaskPlanningAgent:
    """提供注入了模拟客户端的任务规划 Agent。"""
    return TaskPlanningAgent(session=MagicMock(), deepseek_client=mock_deepseek_client)


async def test_valid_agent_types_contains_office() -> None:
    """C-1a：planning 枚举含 office（ADR-062）。"""
    assert "office" in VALID_AGENT_TYPES


async def test_plan_office_task(
    planning_agent: TaskPlanningAgent,
    mock_deepseek_client: MagicMock,
) -> None:
    """C-1b：办公类需求（写报告/PPT）规划为 office 类型。"""
    mock_deepseek_client.chat_completion.return_value = (
        '{"task_name": "行业调研报告", '
        '"description": "调研低代码行业并输出 Word 分析报告", '
        '"agent_type": "office"}'
    )

    result = await planning_agent.plan("帮我调研一下低代码行业，写一份分析报告 docx")

    assert isinstance(result, TaskPlanResult)
    assert result.agent_type == "office"
    assert result.agent_type in VALID_AGENT_TYPES


async def test_planning_prompt_includes_office_guidance(
    planning_agent: TaskPlanningAgent,
) -> None:
    """C-1c：规划提示词包含 office 判据说明。"""
    assert "office" in planning_agent._system_prompt  # noqa: SLF001
    assert "Word" in planning_agent._system_prompt or "文档" in planning_agent._system_prompt  # noqa: SLF001
