"""技能管理 API 端点（ADR-033）。"""

import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_session, get_skill_service
from app.schemas.common import ApiResponse
from app.schemas.skill import SkillDetailResponse, SkillResponse, SkillUpdateRequest
from app.services.skill_service import SkillService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["技能"])


@router.get(
    "",
    response_model=ApiResponse[list[SkillResponse]],
    status_code=status.HTTP_200_OK,
    summary="获取技能列表",
    description="返回全部注册技能及其启用状态（ADR-033）。",
)
async def list_skills(
    session: AsyncSession = Depends(get_async_session),
    service: SkillService = Depends(get_skill_service),
) -> ApiResponse[list[SkillResponse]]:
    """获取技能列表。"""
    logger.info("skill_list_started", extra={"event": "skill_list_started"})
    skills = await service.list_skills(session)
    logger.info(
        "skill_list_completed",
        extra={"event": "skill_list_completed", "count": len(skills)},
    )
    return ApiResponse(code=0, message="ok", data=skills)


@router.get(
    "/{skill_key}",
    response_model=ApiResponse[SkillDetailResponse],
    status_code=status.HTTP_200_OK,
    summary="获取技能详情",
    description="返回技能完整参数 JSON Schema 与返回结构说明（ADR-033）。",
)
async def get_skill(
    skill_key: str,
    session: AsyncSession = Depends(get_async_session),
    service: SkillService = Depends(get_skill_service),
) -> ApiResponse[SkillDetailResponse]:
    """获取技能详情。"""
    detail = await service.get_skill(session, skill_key)
    logger.info(
        "skill_detail_completed",
        extra={"event": "skill_detail_completed", "skill_key": skill_key},
    )
    return ApiResponse(code=0, message="ok", data=detail)


@router.patch(
    "/{skill_key}",
    response_model=ApiResponse[SkillResponse],
    status_code=status.HTTP_200_OK,
    summary="更新技能启用开关",
    description="启用/禁用技能；禁用后 Orchestrator 对话 Agent 不再调用（ADR-033）。",
)
async def update_skill(
    skill_key: str,
    request: SkillUpdateRequest,
    session: AsyncSession = Depends(get_async_session),
    service: SkillService = Depends(get_skill_service),
) -> ApiResponse[SkillResponse]:
    """更新技能启用开关。"""
    logger.info(
        "skill_update_started",
        extra={"event": "skill_update_started", "skill_key": skill_key, "enabled": request.enabled},
    )
    skill = await service.set_skill_enabled(session, skill_key, request.enabled)
    logger.info(
        "skill_update_completed",
        extra={
            "event": "skill_update_completed",
            "skill_key": skill_key,
            "enabled": request.enabled,
        },
    )
    return ApiResponse(code=0, message="ok", data=skill)
