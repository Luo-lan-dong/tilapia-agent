"""会话附件 API 端点（ADR-039 P1）。

会话附件：路径前缀 /sessions/{session_id}/attachments（上传 multipart / 列表）。
任务附件一次性下载：路径前缀 /tasks/{task_id}/attachments（Agent 内部，token 即用即焚）。
"""

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_session
from app.core.errors import BusinessError, ErrorCode
from app.schemas.attachment import AttachmentResponse
from app.schemas.common import ApiResponse
from app.services.attachment_service import (
    AttachmentNotFoundError,
    AttachmentService,
    AttachmentValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["会话附件"])
#: 任务附件一次性下载（Agent 内部通道，非统一响应格式）
download_router = APIRouter(tags=["会话附件"])


def get_attachment_service() -> AttachmentService:
    """获取附件服务实例依赖。"""
    return AttachmentService()


@router.post(
    "",
    response_model=ApiResponse[AttachmentResponse],
    status_code=status.HTTP_201_CREATED,
    summary="上传会话附件",
    description=(
        "上传图片或 zip 压缩包（multipart/form-data）；图片单文件 ≤10MB"
        "（会话图片总量 ≤50MB），zip 单文件 ≤200MB。"
    ),
)
async def upload_attachment(
    session_id: uuid.UUID,
    upload: UploadFile,
    service: AttachmentService = Depends(get_attachment_service),
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[AttachmentResponse]:
    """上传会话附件。"""
    logger.info(
        "attachment_upload_api_started",
        extra={
            "event": "attachment_upload_api_started",
            "session_id": str(session_id),
            "file_name": upload.filename,
        },
    )
    try:
        record = await service.save_upload(session, session_id, upload)
    except AttachmentValidationError as exc:
        raise BusinessError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            biz_code=ErrorCode.ATTACHMENT_INVALID,
            detail=str(exc),
        ) from exc
    return ApiResponse(
        code=0,
        message="ok",
        data=AttachmentResponse(
            id=record.id,
            file_name=record.file_name,
            mime=record.mime,
            size=record.size,
            kind=record.kind,
            created_at=record.created_at,
        ),
    )


@router.get(
    "",
    response_model=ApiResponse[list[AttachmentResponse]],
    status_code=status.HTTP_200_OK,
    summary="查询会话附件列表",
    description="返回会话下全部附件（按上传时间升序）。",
)
async def list_attachments(
    session_id: uuid.UUID,
    service: AttachmentService = Depends(get_attachment_service),
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[list[AttachmentResponse]]:
    """查询会话附件列表。"""
    records = await service.list_by_session(session, session_id)
    return ApiResponse(
        code=0,
        message="ok",
        data=[
            AttachmentResponse(
                id=record.id,
                file_name=record.file_name,
                mime=record.mime,
                size=record.size,
                kind=record.kind,
                created_at=record.created_at,
            )
            for record in records
        ],
    )


@router.get(
    "/{attachment_id}/content",
    summary="预览会话附件原图",
    description="按会话归属返回附件原始文件（图片预览）；会话不匹配或附件不存在返回 404。",
)
async def preview_session_attachment(
    session_id: uuid.UUID,
    attachment_id: uuid.UUID,
    service: AttachmentService = Depends(get_attachment_service),
    session: AsyncSession = Depends(get_async_session),
) -> FileResponse:
    """会话附件原图预览（前端历史消息图片展示）。"""
    from app.core.config import get_settings

    logger.info(
        "attachment_preview_started",
        extra={
            "event": "attachment_preview_started",
            "session_id": str(session_id),
            "attachment_id": str(attachment_id),
        },
    )
    try:
        record = await service.get(session, attachment_id)
    except AttachmentNotFoundError as exc:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.ATTACHMENT_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if str(record.session_id) != str(session_id):
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.ATTACHMENT_NOT_FOUND,
            detail="附件不属于该会话",
        )
    path = Path(get_settings().attachment_storage_dir) / record.storage_path
    if not path.is_file():
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.ATTACHMENT_NOT_FOUND,
            detail="附件文件已缺失",
        )
    return FileResponse(path=path, media_type=record.mime, filename=record.file_name)


@download_router.get(
    "/{attachment_id}",
    summary="任务附件一次性下载（Agent 内部，ADR-039）",
    description="按一次性 token 下载任务附件原始文件；token 无效/过期/错配返回 401。非统一响应格式（文件流）。",
)
async def download_task_attachment(
    task_id: uuid.UUID,
    attachment_id: uuid.UUID,
    token: str = Query(..., description="派发 payload 下发的一次性下载 token"),
) -> FileResponse:
    """Agent 任务附件下载（token 即用即焚）。"""
    logger.info(
        "attachment_download_started",
        extra={
            "event": "attachment_download_started",
            "task_id": str(task_id),
            "attachment_id": str(attachment_id),
        },
    )
    path = AttachmentService().resolve_download(
        str(task_id), str(attachment_id), token
    )
    if path is None:
        raise BusinessError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            biz_code=ErrorCode.RESOURCE_SYNC_FAILED,
            detail="附件下载凭证无效或已过期",
        )
    return FileResponse(path=path, filename=path.name)
