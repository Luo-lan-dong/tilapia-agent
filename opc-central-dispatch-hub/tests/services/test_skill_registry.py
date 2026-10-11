"""技能注册表测试（ADR-033）。"""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hub_skill import HubSkill
from app.skills.registry import (
    SKILL_REGISTRY,
    find_skill,
    get_enabled_skills,
    to_openai_tools,
)


def test_registry_contains_mvp_skills() -> None:
    """注册表包含全部 MVP 技能且 skill_key 无冲突（数量跟随注册表增长）。"""
    keys = [cls.name for cls in SKILL_REGISTRY]
    assert len(keys) == len(set(keys))
    assert {
        "find_idle_nodes",
        "smart_dispatch",
        "create_task",
        "get_node_metrics",
        "get_task_status",
        "generate_spec_docs",
        "history_recall",
        "query_logs",
        "query_trace",
        "fetch_node_logs",
    }.issubset(set(keys))


def test_registry_schema_contract() -> None:
    """每个技能的 parameters 为合法 JSON Schema 且关键字段齐全。"""
    for cls in SKILL_REGISTRY:
        skill = cls()
        assert skill.parameters.get("type") == "object"
        assert "properties" in skill.parameters
        assert skill.description
        assert skill.display_name
        assert skill.returns


def test_find_skill_unknown_returns_none() -> None:
    """未知 skill_key 返回 None（LLM 幻觉工具名兜底）。"""
    assert find_skill("no_such_skill") is None
    assert find_skill("") is None


def test_to_openai_tools_structure() -> None:
    """tools 定义符合 OpenAI function calling 格式。"""
    tools = to_openai_tools([cls() for cls in SKILL_REGISTRY])
    assert len(tools) == len(SKILL_REGISTRY)
    for tool in tools:
        assert tool["type"] == "function"
        assert tool["function"]["name"]
        assert tool["function"]["parameters"]["type"] == "object"


async def test_get_enabled_skills_filters_disabled(db_session: AsyncSession) -> None:
    """hub_skills.enabled=false 的技能被过滤。"""
    await db_session.execute(delete(HubSkill))
    db_session.add(HubSkill(skill_key="get_node_metrics", name="x", description="d", enabled=False))
    await db_session.commit()

    skills = await get_enabled_skills(db_session)
    enabled_keys = {skill.name for skill in skills}

    assert "get_node_metrics" not in enabled_keys
    assert "find_idle_nodes" in enabled_keys
    assert len(enabled_keys) == len(SKILL_REGISTRY) - 1


async def test_get_enabled_skills_defaults_all_enabled(db_session: AsyncSession) -> None:
    """无 DB 记录时全部启用。"""
    await db_session.execute(delete(HubSkill))
    await db_session.commit()
    skills = await get_enabled_skills(db_session)
    assert len(skills) == len(SKILL_REGISTRY)
