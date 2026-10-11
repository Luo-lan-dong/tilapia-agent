"""SOP 知识管理 API 端点（ADR-051）。"""

import logging
import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_session
from app.core.errors import BusinessError, ErrorCode
from app.models.sop_record import SopRecord
from app.schemas.common import ApiResponse
from app.schemas.sop import SopCreate, SopListResponse, SopResponse, SopUpdate
from app.services.sop_service import SopService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["SOP 知识"])


def get_sop_service() -> SopService:
    """获取 SOP 服务实例依赖。"""
    return SopService()


async def _get_or_404(
    session: AsyncSession, service: SopService, record_id: uuid.UUID
) -> SopRecord:
    """按 ID 取条目，不存在抛 209001。"""
    record = await service.get_record(session, record_id)
    if record is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.SOP_NOT_FOUND,
            detail=f"SOP 不存在: {record_id}",
        )
    return record


@router.get(
    "",
    response_model=ApiResponse[SopListResponse],
    response_model_exclude_unset=True,
    summary="查询 SOP 列表",
    description="keyword 模糊（名称/场景/正文）+ scene/source 过滤 + 分页。",
)
async def list_sops(
    keyword: str | None = Query(default=None, max_length=100),
    scene: str | None = Query(default=None, max_length=30),
    source: str | None = Query(default=None, max_length=16),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_async_session),
    service: SopService = Depends(get_sop_service),
) -> ApiResponse[SopListResponse]:
    """查询 SOP 分页列表。"""
    records, total = await service.list_records(
        session, keyword=keyword, scene=scene, source=source, page=page, page_size=page_size
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=SopListResponse(
            total=total,
            page=page,
            page_size=page_size,
            items=[SopResponse.model_validate(record) for record in records],
        ),
    )


@router.post(
    "",
    response_model=ApiResponse[SopResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_201_CREATED,
    summary="创建 SOP",
)
async def create_sop(
    data: SopCreate,
    session: AsyncSession = Depends(get_async_session),
    service: SopService = Depends(get_sop_service),
) -> ApiResponse[SopResponse]:
    """创建 SOP（同名 209002）。"""
    try:
        record = await service.save(
            session,
            name=data.name,
            scene=data.scene,
            content=data.content,
            source=data.source,
        )
    except BusinessError as exc:
        raise exc
    return ApiResponse(code=0, message="ok", data=SopResponse.model_validate(record))


@router.get(
    "/{record_id}",
    response_model=ApiResponse[SopResponse],
    response_model_exclude_unset=True,
    summary="查询 SOP 详情",
)
async def get_sop(
    record_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: SopService = Depends(get_sop_service),
) -> ApiResponse[SopResponse]:
    """查询 SOP 详情。"""
    record = await _get_or_404(session, service, record_id)
    return ApiResponse(code=0, message="ok", data=SopResponse.model_validate(record))


@router.patch(
    "/{record_id}",
    response_model=ApiResponse[SopResponse],
    response_model_exclude_unset=True,
    summary="编辑 SOP",
)
async def update_sop(
    record_id: uuid.UUID,
    data: SopUpdate,
    session: AsyncSession = Depends(get_async_session),
    service: SopService = Depends(get_sop_service),
) -> ApiResponse[SopResponse]:
    """编辑 SOP（纠正固化的流程内容）。"""
    record = await _get_or_404(session, service, record_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] != record.name:
        dup = await service.list_records(session, keyword=changes["name"], page_size=1)
        if any(item.name == changes["name"] and item.id != record.id for item in dup[0]):
            raise BusinessError(
                status_code=status.HTTP_409_CONFLICT,
                biz_code=ErrorCode.SOP_NAME_DUPLICATE,
                detail=f"同名 SOP 已存在：{changes['name']}",
            )
    for field, value in changes.items():
        setattr(record, field, value)
    await session.commit()
    await session.refresh(record)
    return ApiResponse(code=0, message="ok", data=SopResponse.model_validate(record))


@router.delete(
    "/{record_id}",
    response_model=ApiResponse[None],
    response_model_exclude_unset=True,
    summary="删除 SOP",
)
async def delete_sop(
    record_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: SopService = Depends(get_sop_service),
) -> ApiResponse[None]:
    """删除 SOP。"""
    record = await _get_or_404(session, service, record_id)
    await service.delete_record(session, record)
    return ApiResponse(code=0, message="ok", data=None)
