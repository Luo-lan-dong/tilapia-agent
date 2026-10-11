"""经验记忆管理 API 端点（ADR-050）。"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_session, get_experience_service
from app.core.errors import BusinessError, ErrorCode
from app.models.experience_record import ExperienceRecord
from app.schemas.common import ApiResponse
from app.schemas.experience import (
    ExperienceListResponse,
    ExperienceResponse,
    ExperienceUpdate,
)
from app.services.experience_service import ExperienceService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["经验记忆"])


@router.get(
    "",
    response_model=ApiResponse[ExperienceListResponse],
    response_model_exclude_unset=True,
    summary="查询经验条目列表",
    description="keyword 模糊搜索（工具/签名/现象/根因/解法）+ status 过滤 + 分页。",
)
async def list_records(
    keyword: str | None = Query(default=None, max_length=100),
    status_filter: str | None = Query(default=None, alias="status", max_length=16),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_async_session),
    service: ExperienceService = Depends(get_experience_service),
) -> ApiResponse[ExperienceListResponse]:
    """查询经验条目分页列表。"""
    records, total = await service.list_records(
        session,
        keyword=keyword,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=ExperienceListResponse(
            total=total,
            page=page,
            page_size=page_size,
            items=[ExperienceResponse.model_validate(record) for record in records],
        ),
    )


@router.get(
    "/{record_id}",
    response_model=ApiResponse[ExperienceResponse],
    response_model_exclude_unset=True,
    summary="查询经验条目详情",
)
async def get_record(
    record_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: ExperienceService = Depends(get_experience_service),
) -> ApiResponse[ExperienceResponse]:
    """查询经验条目详情。"""
    record = await _get_or_404(session, service, record_id)
    return ApiResponse(code=0, message="ok", data=ExperienceResponse.model_validate(record))


@router.patch(
    "/{record_id}",
    response_model=ApiResponse[ExperienceResponse],
    response_model_exclude_unset=True,
    summary="编辑经验条目",
    description="纠正 AI 记错的结论或回填根因/解法；置 status=resolved 时自动写 resolved_at。",
)
async def update_record(
    record_id: uuid.UUID,
    data: ExperienceUpdate,
    session: AsyncSession = Depends(get_async_session),
    service: ExperienceService = Depends(get_experience_service),
) -> ApiResponse[ExperienceResponse]:
    """编辑经验条目。"""
    record = await _get_or_404(session, service, record_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("status") == "resolved" and record.status != "resolved":
        record.resolved_at = datetime.now(UTC)
    for field, value in changes.items():
        setattr(record, field, value)
    await session.commit()
    await session.refresh(record)
    return ApiResponse(code=0, message="ok", data=ExperienceResponse.model_validate(record))


@router.delete(
    "/{record_id}",
    response_model=ApiResponse[None],
    response_model_exclude_unset=True,
    summary="删除经验条目",
)
async def delete_record(
    record_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: ExperienceService = Depends(get_experience_service),
) -> ApiResponse[None]:
    """删除经验条目。"""
    record = await _get_or_404(session, service, record_id)
    await service.delete_record(session, record)
    return ApiResponse(code=0, message="ok", data=None)


async def _get_or_404(
    session: AsyncSession,
    service: ExperienceService,
    record_id: uuid.UUID,
) -> ExperienceRecord:
    """按 ID 取条目，不存在抛 208001。"""
    record = await service.get_record(session, record_id)
    if record is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.EXPERIENCE_NOT_FOUND,
            detail=f"经验条目不存在: {record_id}",
        )
    return record
