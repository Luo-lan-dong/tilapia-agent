"""任务规划 Agent 单元测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.planning import (
    VALID_AGENT_TYPES,
    TaskPlanningAgent,
    TaskPlanningError,
    TaskPlanResult,
)
from app.clients.deepseek import DeepSeekAPIError


@pytest.fixture
def mock_session() -> MagicMock:
    """提供模拟的数据库会话。"""
    return MagicMock()


@pytest.fixture
def mock_deepseek_client() -> MagicMock:
    """提供模拟的 DeepSeekClient 实例。"""
    client = MagicMock()
    client.chat_completion = AsyncMock()
    return client


@pytest.fixture
def planning_agent(
    mock_session: MagicMock,
    mock_deepseek_client: MagicMock,
) -> TaskPlanningAgent:
    """提供注入了模拟客户端的任务规划 Agent。"""
    return TaskPlanningAgent(session=mock_session, deepseek_client=mock_deepseek_client)


async def test_plan_success(
    planning_agent: TaskPlanningAgent,
    mock_deepseek_client: MagicMock,
) -> None:
    """LLM 返回旧类型 programming 时应归一化为 code（ADR-016）。"""
    requirement = "帮我开发一个官网落地页"
    mock_deepseek_client.chat_completion.return_value = (
        '{"task_name": "官网落地页开发", '
        '"description": "使用 React 开发响应式官网落地页", '
        '"agent_type": "programming"}'
    )

    result = await planning_agent.plan(requirement)

    assert isinstance(result, TaskPlanResult)
    assert result.task_name == "官网落地页开发"
    assert result.description == "使用 React 开发响应式官网落地页"
    assert result.agent_type == "code"
    assert result.agent_type in VALID_AGENT_TYPES
    mock_deepseek_client.chat_completion.assert_awaited_once_with(
        system_prompt=planning_agent._system_prompt,
        user_prompt=requirement,
    )


async def test_plan_success_with_code_type(
    planning_agent: TaskPlanningAgent,
    mock_deepseek_client: MagicMock,
) -> None:
    """LLM 直接返回 code 类型时应原样通过。"""
    mock_deepseek_client.chat_completion.return_value = (
        '{"task_name": "官网落地页开发", '
        '"description": "使用 React 开发响应式官网落地页", '
        '"agent_type": "code"}'
    )

    result = await planning_agent.plan("帮我开发一个官网落地页")

    assert result.agent_type == "code"


async def test_plan_success_with_markdown_code_block(
    planning_agent: TaskPlanningAgent,
    mock_deepseek_client: MagicMock,
) -> None:
    """DeepSeek 返回 Markdown 代码块时也应正确解析。"""
    mock_deepseek_client.chat_completion.return_value = (
        "```json\n"
        '{"task_name": "品牌海报设计", '
        '"description": "设计活动主视觉海报", '
        '"agent_type": "design"}\n'
        "```"
    )

    result = await planning_agent.plan("设计一张活动海报")

    assert result.task_name == "品牌海报设计"
    assert result.agent_type == "design"


async def test_plan_default_description(
    planning_agent: TaskPlanningAgent,
    mock_deepseek_client: MagicMock,
) -> None:
    """description 缺失时应默认使用原始需求。"""
    requirement = "帮我写一段产品介绍文案"
    mock_deepseek_client.chat_completion.return_value = (
        '{"task_name": "产品介绍文案", "description": "", "agent_type": "copywriting"}'
    )

    result = await planning_agent.plan(requirement)

    assert result.description == requirement
    assert result.agent_type == "copywriting"


async def test_plan_unknown_agent_type(
    planning_agent: TaskPlanningAgent,
    mock_deepseek_client: MagicMock,
) -> None:
    """未知 agent_type 时应抛出 TaskPlanningError。"""
    mock_deepseek_client.chat_completion.return_value = (
        '{"task_name": "未知任务", "description": "测试", "agent_type": "music"}'
    )

    with pytest.raises(TaskPlanningError) as exc_info:
        await planning_agent.plan("帮我写一首歌")

    assert "music" in str(exc_info.value)
    assert "agent_type" in str(exc_info.value) or "未知" in str(exc_info.value)


async def test_plan_missing_task_name(
    planning_agent: TaskPlanningAgent,
    mock_deepseek_client: MagicMock,
) -> None:
    """缺少 task_name 时应抛出 TaskPlanningError。"""
    mock_deepseek_client.chat_completion.return_value = (
        '{"description": "测试", "agent_type": "video"}'
    )

    with pytest.raises(TaskPlanningError, match="task_name"):
        await planning_agent.plan("帮我剪一个视频")


async def test_plan_missing_agent_type(
    planning_agent: TaskPlanningAgent,
    mock_deepseek_client: MagicMock,
) -> None:
    """缺少 agent_type 时应抛出 TaskPlanningError。"""
    mock_deepseek_client.chat_completion.return_value = (
        '{"task_name": "没有类型的任务", "description": "测试"}'
    )

    with pytest.raises(TaskPlanningError, match="agent_type"):
        await planning_agent.plan("帮我做一件事")


async def test_plan_invalid_json(
    planning_agent: TaskPlanningAgent,
    mock_deepseek_client: MagicMock,
) -> None:
    """DeepSeek 返回非 JSON 内容时应抛出 TaskPlanningError。"""
    mock_deepseek_client.chat_completion.return_value = "这不是 JSON"

    with pytest.raises(TaskPlanningError) as exc_info:
        await planning_agent.plan("帮我开发一个官网落地页")

    assert "JSON" in str(exc_info.value)


async def test_plan_deepseek_api_error(
    planning_agent: TaskPlanningAgent,
    mock_deepseek_client: MagicMock,
) -> None:
    """DeepSeek API 异常时应透传 DeepSeekAPIError。"""
    mock_deepseek_client.chat_completion.side_effect = DeepSeekAPIError(
        status_code=503,
        message="Service Unavailable",
    )

    with pytest.raises(DeepSeekAPIError) as exc_info:
        await planning_agent.plan("帮我开发一个官网落地页")

    assert exc_info.value.status_code == 503
    assert "Service Unavailable" in exc_info.value.message


async def test_plan_empty_requirement(
    planning_agent: TaskPlanningAgent,
    mock_deepseek_client: MagicMock,
) -> None:
    """空需求时应抛出 TaskPlanningError，且不调用 DeepSeek。"""
    with pytest.raises(TaskPlanningError, match="需求不能为空"):
        await planning_agent.plan("   ")

    mock_deepseek_client.chat_completion.assert_not_awaited()
