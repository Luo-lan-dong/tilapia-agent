"""任务产物资源管理 API 端点（ADR-028）。

路径前缀：/tasks/{task_id}/artifacts。
"""

import logging
import uuid

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_session
from app.core.errors import BusinessError, ErrorCode
from app.models.task import Task
from app.schemas.common import ApiResponse
from app.services.artifact_service import artifact_service

logger = logging.getLogger(__name__)

#: 文本预览的最大字节数（与 artifact_service 上限一致）
_TEXT_PREVIEW_LIMIT = 10 * 1024 * 1024

router = APIRouter(tags=["任务产物"])


def _artifact_runtime_error_code(exc: RuntimeError) -> int:
    """将产物拉取的 RuntimeError 映射为业务错误码（api-standards §3）。"""
    message = str(exc)
    if "离线" in message:
        return ErrorCode.AGENT_NODE_OFFLINE
    if "尚未分配" in message:
        return ErrorCode.AGENT_NODE_NOT_FOUND
    return ErrorCode.RESOURCE_SYNC_FAILED


class ArtifactTreeNode(BaseModel):
    """文件清单条目。"""

    path: str = Field(..., description="工作目录内相对路径")
    size: int = Field(0, description="文件大小（字节）")
    mtime: float = Field(0, description="最后修改时间（Unix 秒）")


class ArtifactTreeResponse(BaseModel):
    """文件清单响应。"""

    task_id: str = Field(..., description="任务 ID")
    updated_at: float | None = Field(None, description="清单更新时间（Unix 秒）")
    truncated: bool = Field(False, description="清单是否被截断")
    files: list[ArtifactTreeNode] = Field(default_factory=list, description="文件列表")
    cached_zip: bool = Field(False, description="是否已有可下载的打包缓存")


class ArtifactFileResponse(BaseModel):
    """单文件预览响应。"""

    path: str = Field(..., description="相对路径")
    binary: bool = Field(False, description="是否为二进制文件（binary=true 时无 content）")
    size: int = Field(0, description="文件大小（字节）")
    content: str | None = Field(None, description="文本内容（二进制文件为 null）")


async def _load_task(task_id: uuid.UUID, session: AsyncSession) -> Task:
    """加载任务；不存在时抛 404 + 201001。"""
    task = await session.get(Task, task_id)
    if task is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.TASK_NOT_FOUND,
            detail=f"任务 {task_id} 不存在",
        )
    return task


@router.get(
    "/tree",
    response_model=ApiResponse[ArtifactTreeResponse],
    response_model_exclude_unset=True,
    summary="获取任务产物文件清单",
    description="返回 hub 缓存的最近一次 agent.file_tree 清单；无缓存返回空列表。",
)
async def get_artifacts_tree(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[ArtifactTreeResponse]:
    """返回产物文件清单（不触发网络请求）。"""
    logger.info(
        "artifact_tree_get_started",
        extra={"event": "artifact_tree_get_started", "task_id": str(task_id)},
    )
    tree = artifact_service.get_tree(str(task_id))
    if tree is None:
        return ApiResponse(
            code=0,
            message="ok",
            data=ArtifactTreeResponse(task_id=str(task_id), files=[]),
        )
    return ApiResponse(
        code=0,
        message="ok",
        data=ArtifactTreeResponse(
            task_id=str(task_id),
            updated_at=tree.get("updated_at"),
            truncated=tree.get("truncated", False),
            files=[ArtifactTreeNode(**item) for item in tree.get("files", [])],
            cached_zip=artifact_service.get_cached_zip_path(str(task_id)) is not None,
        ),
    )


@router.get(
    "/file",
    response_model=ApiResponse[ArtifactFileResponse],
    response_model_exclude_unset=True,
    summary="预览产物文件",
    description="优先读取缓存；缓存缺失时向 Agent 按需拉取（路径越界/离线/超时返回业务错误）。",
)
async def get_artifact_file(
    task_id: uuid.UUID,
    path: str = Query(..., description="工作目录内相对路径"),
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[ArtifactFileResponse]:
    """返回单文件文本预览。"""
    logger.info(
        "artifact_file_get_started",
        extra={"event": "artifact_file_get_started", "task_id": str(task_id), "path": path},
    )
    task = await _load_task(task_id, session)
    local_path = artifact_service.get_cached_file_path(str(task_id), path)
    if local_path is None:
        try:
            local_path = await artifact_service.fetch_file(
                str(task_id), str(task.assigned_node_id) if task.assigned_node_id else None, path
            )
        except ValueError as exc:
            raise BusinessError(
                status_code=status.HTTP_404_NOT_FOUND,
                biz_code=ErrorCode.RESOURCE_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except RuntimeError as exc:
            raise BusinessError(
                status_code=status.HTTP_409_CONFLICT,
                biz_code=_artifact_runtime_error_code(exc),
                detail=str(exc),
            ) from exc

    data = local_path.read_bytes()
    if len(data) > _TEXT_PREVIEW_LIMIT:
        raise BusinessError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            biz_code=ErrorCode.RESOURCE_SYNC_FAILED,
            detail="文件超出预览大小上限（10MB）",
        )
    try:
        content = data.decode("utf-8")
        binary = False
    except UnicodeDecodeError:
        binary = True
        content = None
    return ApiResponse(
        code=0,
        message="ok",
        data=ArtifactFileResponse(path=path, binary=binary, size=len(data), content=content),
    )


@router.get(
    "/download",
    summary="打包下载任务产物",
    description="请求 Agent 打包工作目录（zip），hub 落盘后以 FileResponse 返回；已有缓存直接下载。",
)
async def download_artifacts(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> FileResponse:
    """打包下载产物 zip。"""
    logger.info(
        "artifact_download_started",
        extra={"event": "artifact_download_started", "task_id": str(task_id)},
    )
    task = await _load_task(task_id, session)
    zip_path = artifact_service.get_cached_zip_path(str(task_id))
    if zip_path is None:
        try:
            zip_path = await artifact_service.fetch_zip(
                str(task_id), str(task.assigned_node_id) if task.assigned_node_id else None
            )
        except RuntimeError as exc:
            raise BusinessError(
                status_code=status.HTTP_409_CONFLICT,
                biz_code=_artifact_runtime_error_code(exc),
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise BusinessError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                biz_code=ErrorCode.RESOURCE_SYNC_FAILED,
                detail=str(exc),
            ) from exc
    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=f"task-{task_id}-artifacts.zip",
    )


@router.get(
    "/handoff-bundle",
    summary="现场迁移 bundle 一次性下载（Agent 内部，ADR-034）",
    description="按一次性 token 下载任务链产物包；token 无效/过期返回 401。非统一响应格式（文件流）。",
)
async def download_handoff_bundle(
    task_id: uuid.UUID,
    token: str = Query(..., description="派发 payload 下发的一次性下载 token"),
) -> FileResponse:
    """Agent 现场迁移 bundle 下载（token 即用即焚）。"""
    from app.services.task_handoff_service import resolve_bundle_download

    logger.info(
        "handoff_bundle_download_started",
        extra={"event": "handoff_bundle_download_started", "task_id": str(task_id)},
    )
    bundle_path = resolve_bundle_download(str(task_id), token)
    if bundle_path is None:
        raise BusinessError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            biz_code=ErrorCode.RESOURCE_SYNC_FAILED,
            detail="handoff bundle 下载凭证无效或已过期",
        )
    return FileResponse(
        path=bundle_path,
        media_type="application/zip",
        filename=f"task-{task_id}-handoff.zip",
    )


@router.get(
    "/context",
    response_model=ApiResponse[dict],
    response_model_exclude_unset=True,
    summary="查询任务链项目上下文记忆",
    description="返回该任务所属任务链最新的 project_context.md（ADR-034）；无上下文时 data.context_md 为 null。",
)
async def get_task_chain_context(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[dict]:
    """查询任务链最新项目上下文记忆。"""
    from app.services.task_handoff_service import chain_root, get_chain_context

    task = await _load_task(task_id, session)
    context_md = await get_chain_context(session, chain_root(task))
    return ApiResponse(
        code=0,
        message="ok",
        data={"root_task_id": str(chain_root(task)), "context_md": context_md},
    )
