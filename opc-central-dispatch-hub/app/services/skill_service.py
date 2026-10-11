"""技能管理服务层（ADR-033）：注册表元数据 + hub_skills 启用开关。"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import contextvars

from app.core.errors import BusinessError, ErrorCode
from app.models.hub_skill import HubSkill
from app.schemas.skill import SkillDetailResponse, SkillResponse
from app.skills.base import Skill
from app.skills.registry import SKILL_REGISTRY, find_skill

logger = logging.getLogger(__name__)


def _bind_request_id() -> None:
    """将当前上下文的 request_id 绑定到日志上下文。"""
    request_id = contextvars.get_contextvars().get("request_id")
    if request_id:
        contextvars.bind_contextvars(request_id=request_id)


def _parameters_summary(skill: Skill) -> str:
    """从 JSON Schema 生成人类可读的参数摘要。"""
    properties = skill.parameters.get("properties", {})
    if not properties:
        return "无参数"
    parts = []
    for prop_name, prop in properties.items():
        required = prop_name in skill.parameters.get("required", [])
        desc = str(prop.get("description", "")).split("；")[0].split("。")[0]
        parts.append(f"{'必填' if required else '可选'} {prop_name}：{desc}")
    return "；".join(parts)


def _build_response(skill: Skill, enabled: bool, *, detail: bool = False) -> SkillResponse:
    """构造技能响应对象。"""
    response = SkillResponse(
        skill_key=skill.name,
        name=skill.display_name,
        description=skill.description,
        parameters_summary=_parameters_summary(skill),
        returns=skill.returns,
        enabled=enabled,
    )
    if detail:
        return SkillDetailResponse(
            **response.model_dump(),
            parameters=skill.parameters,
        )
    return response


class SkillService:
    """技能管理服务。"""

    async def list_skills(self, session: AsyncSession) -> list[SkillResponse]:
        """列出全部注册技能及其启用状态（无 DB 记录默认启用）。"""
        _bind_request_id()
        enabled_map = await self._load_enabled_map(session)
        return [_build_response(cls(), enabled_map.get(cls.name, True)) for cls in SKILL_REGISTRY]

    async def get_skill(self, session: AsyncSession, skill_key: str) -> SkillDetailResponse:
        """查询单个技能详情（含完整参数 JSON Schema）。"""
        _bind_request_id()
        skill = find_skill(skill_key)
        if skill is None:
            raise BusinessError(
                status_code=404,
                biz_code=ErrorCode.SKILL_NOT_FOUND,
                detail=f"技能不存在：{skill_key}",
            )
        enabled_map = await self._load_enabled_map(session)
        return _build_response(skill, enabled_map.get(skill_key, True), detail=True)  # type: ignore[return-value]

    async def set_skill_enabled(
        self,
        session: AsyncSession,
        skill_key: str,
        enabled: bool,
    ) -> SkillResponse:
        """设置技能启用开关（无记录时按代码注册信息补建）。"""
        _bind_request_id()
        skill = find_skill(skill_key)
        if skill is None:
            raise BusinessError(
                status_code=404,
                biz_code=ErrorCode.SKILL_NOT_FOUND,
                detail=f"技能不存在：{skill_key}",
            )
        result = await session.execute(select(HubSkill).where(HubSkill.skill_key == skill_key))
        row = result.scalar_one_or_none()
        if row is None:
            row = HubSkill(
                skill_key=skill_key,
                name=skill.display_name,
                description=skill.description,
                enabled=enabled,
            )
            session.add(row)
        else:
            row.enabled = enabled
        await session.commit()
        logger.info(
            "skill_enabled_updated",
            extra={
                "event": "skill_enabled_updated",
                "skill_key": skill_key,
                "enabled": enabled,
            },
        )
        return _build_response(skill, enabled)

    async def _load_enabled_map(self, session: AsyncSession) -> dict[str, bool]:
        """读取 hub_skills 启用状态映射（读取失败时返回空映射=全启用）。"""
        try:
            result = await session.execute(select(HubSkill))
            return {row.skill_key: row.enabled for row in result.scalars().all()}
        except Exception:  # noqa: BLE001 — 表未迁移时兜底
            logger.warning(
                "skill_enabled_map_lookup_failed",
                extra={"event": "skill_enabled_map_lookup_failed"},
                exc_info=True,
            )
            return {}
