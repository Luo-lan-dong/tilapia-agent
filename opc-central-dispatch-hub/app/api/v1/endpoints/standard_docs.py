"""开发规则文档 API 端点（ADR-048）。"""

import logging
import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_session, get_standard_doc_service
from app.core.errors import BusinessError, ErrorCode
from app.schemas.common import ApiResponse
from app.schemas.standard_doc import (
    StandardDocCreate,
    StandardDocDetailResponse,
    StandardDocListResponse,
    StandardDocResponse,
    StandardDocUpdate,
)
from app.services.standard_doc_service import (
    StandardDocService,
    resolve_rule_doc_download,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["规则文档"])


@router.get(
    "",
    response_model=ApiResponse[StandardDocListResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="查询规则文档列表",
    description="返回全部规则文档元数据（不含正文，ADR-048）。",
)
async def list_standard_docs(
    session: AsyncSession = Depends(get_async_session),
    service: StandardDocService = Depends(get_standard_doc_service),
) -> ApiResponse[StandardDocListResponse]:
    """查询规则文档列表。"""
    docs = await service.list_docs(session)
    return ApiResponse(
        code=0,
        message="ok",
        data=StandardDocListResponse(
            total=len(docs),
            items=[StandardDocResponse.model_validate(doc) for doc in docs],
        ),
    )


@router.get(
    "/download/rule-doc",
    summary="规则文档下载（Agent 内部，ADR-048）",
    description="按派发 payload 下发的 token 下载规则文档；token 无效/过期返回 401。非统一响应格式（文件流）。",
)
async def download_rule_doc(
    token: str = Query(..., description="派发 payload 下发的下载 token"),
) -> Response:
    """Agent 规则文档下载（TTL 内可重复下载，支持重试）。"""
    resolved = resolve_rule_doc_download(token)
    if resolved is None:
        raise BusinessError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            biz_code=ErrorCode.DOC_DOWNLOAD_TOKEN_INVALID,
            detail="规则文档下载凭证无效或已过期",
        )
    file_name, content = resolved
    logger.info(
        "rule_doc_downloaded",
        extra={"event": "rule_doc_downloaded", "file_name": file_name, "size": len(content)},
    )
    return Response(
        content=content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{file_name}.md"'},
    )


@router.get(
    "/{doc_id}",
    response_model=ApiResponse[StandardDocDetailResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="查询规则文档详情",
    description="返回规则文档详情（含 Markdown 全文）。",
)
async def get_standard_doc(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: StandardDocService = Depends(get_standard_doc_service),
) -> ApiResponse[StandardDocDetailResponse]:
    """查询规则文档详情。"""
    doc = await service.get_doc(session, doc_id)
    if doc is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.DOC_NOT_FOUND,
            detail=f"规则文档不存在: {doc_id}",
        )
    return ApiResponse(code=0, message="ok", data=StandardDocDetailResponse.model_validate(doc))


@router.post(
    "",
    response_model=ApiResponse[StandardDocDetailResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_201_CREATED,
    summary="创建规则文档",
    description="创建规则文档（name 唯一）；上传 .md 由前端读取文本后调用本接口。",
)
async def create_standard_doc(
    data: StandardDocCreate,
    session: AsyncSession = Depends(get_async_session),
    service: StandardDocService = Depends(get_standard_doc_service),
) -> ApiResponse[StandardDocDetailResponse]:
    """创建规则文档。"""
    doc = await service.create_doc(session, data)
    return ApiResponse(code=0, message="ok", data=StandardDocDetailResponse.model_validate(doc))


@router.put(
    "/{doc_id}",
    response_model=ApiResponse[StandardDocDetailResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="更新规则文档",
    description="更新规则文档（name 不可改）；每次更新 version 自增，已建任务使用创建时快照不受影响。",
)
async def update_standard_doc(
    doc_id: uuid.UUID,
    data: StandardDocUpdate,
    session: AsyncSession = Depends(get_async_session),
    service: StandardDocService = Depends(get_standard_doc_service),
) -> ApiResponse[StandardDocDetailResponse]:
    """更新规则文档。"""
    doc = await service.get_doc(session, doc_id)
    if doc is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.DOC_NOT_FOUND,
            detail=f"规则文档不存在: {doc_id}",
        )
    doc = await service.update_doc(session, doc, data)
    return ApiResponse(code=0, message="ok", data=StandardDocDetailResponse.model_validate(doc))


@router.delete(
    "/{doc_id}",
    response_model=ApiResponse[None],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="删除规则文档",
    description="删除规则文档；已创建任务的快照不受影响，派发时跳过缺失文档。",
)
async def delete_standard_doc(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: StandardDocService = Depends(get_standard_doc_service),
) -> ApiResponse[None]:
    """删除规则文档。"""
    doc = await service.get_doc(session, doc_id)
    if doc is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.DOC_NOT_FOUND,
            detail=f"规则文档不存在: {doc_id}",
        )
    await service.delete_doc(session, doc)
    return ApiResponse(code=0, message="ok", data=None)
