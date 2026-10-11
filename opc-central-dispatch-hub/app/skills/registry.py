"""技能注册表：代码注册 + 数据库启用开关（ADR-033）。

技能本体在代码中注册（保证参数 Schema 类型安全），``hub_skills`` 表仅
维护展示元数据与启用开关；表无记录或读取失败时按"全部启用"兜底。
"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hub_skill import HubSkill
from app.skills.artifact_access import ListArtifactsSkill, ReadFileSkill
from app.skills.base import Skill
from app.skills.bt_mcp import (
    BtDeployGuideSkill,
    BtMcpCallSkill,
    BtMcpSchemaSkill,
    BtMcpServersSkill,
    BtMcpSetupSkill,
    BtMcpToolsSkill,
)
from app.skills.deploy_plan import DeployPlanSkill
from app.skills.dispatch_skill import SmartDispatchSkill
from app.skills.experience import ExperienceRecallSkill, ExperienceResolveSkill
from app.skills.fetch_node_logs import FetchNodeLogsSkill
from app.skills.history_recall import HistoryRecallSkill
from app.skills.node_inspection import FindIdleNodesSkill, GetNodeMetricsSkill
from app.skills.orchestrator_todo import OrchestratorTodoSkill
from app.skills.query_logs import QueryLogsSkill
from app.skills.query_trace import QueryTraceSkill
from app.skills.sop_save import SopSaveSkill
from app.skills.spec_generation import GenerateSpecDocsSkill
from app.skills.standard_docs import GenerateStandardDocSkill, ListStandardDocsSkill
from app.skills.task_creation import CreateTaskSkill
from app.skills.task_skills import GetTaskStatusSkill
from app.skills.terminal_skill import RunCommandSkill

logger = logging.getLogger(__name__)

#: MVP 技能注册清单（与迁移种子数据保持一致）
SKILL_REGISTRY: list[type[Skill]] = [
    FindIdleNodesSkill,
    SmartDispatchSkill,
    CreateTaskSkill,
    GetNodeMetricsSkill,
    GetTaskStatusSkill,
    GenerateSpecDocsSkill,
    HistoryRecallSkill,
    QueryLogsSkill,
    QueryTraceSkill,
    FetchNodeLogsSkill,
    ListStandardDocsSkill,
    GenerateStandardDocSkill,
    BtMcpServersSkill,
    BtMcpToolsSkill,
    BtMcpSchemaSkill,
    BtMcpCallSkill,
    BtDeployGuideSkill,
    BtMcpSetupSkill,
    DeployPlanSkill,
    SopSaveSkill,
    OrchestratorTodoSkill,
    ExperienceRecallSkill,
    ExperienceResolveSkill,
    RunCommandSkill,
    ListArtifactsSkill,
    ReadFileSkill,
]


def find_skill(skill_key: str) -> Skill | None:
    """按 skill_key 查找技能实例（注册表内必有，开关由调用方过滤）。"""
    for cls in SKILL_REGISTRY:
        if cls.name == skill_key:
            return cls()
    return None


def to_openai_tools(skills: list[Skill]) -> list[dict[str, Any]]:
    """将技能列表转换为 OpenAI function calling tools 定义。"""
    return [
        {
            "type": "function",
            "function": {
                "name": skill.name,
                "description": skill.description,
                "parameters": skill.parameters,
            },
        }
        for skill in skills
    ]


async def get_enabled_skills(session: AsyncSession) -> list[Skill]:
    """获取当前启用的技能列表（hub_skills.enabled 过滤，失败兜底全启用）。"""
    all_skills = [cls() for cls in SKILL_REGISTRY]
    try:
        result = await session.execute(select(HubSkill))
        disabled = {row.skill_key for row in result.scalars().all() if not row.enabled}
    except Exception:  # noqa: BLE001 — 表不存在/迁移未跑时兜底全启用
        logger.warning(
            "skill_registry_enabled_lookup_failed",
            extra={"event": "skill_registry_enabled_lookup_failed"},
            exc_info=True,
        )
        return all_skills
    return [skill for skill in all_skills if skill.name not in disabled]
