"""会话附件服务（ADR-039 P1）。

职责：
- 图片上传：白名单/单文件大小/会话总量校验，落盘 attachment_storage_dir；
- 任务关联：创建任务时把会话附件回填 task_id；
- 派发 token：任务派发时为附件签发一次性下载 token（同 ADR-034 handoff 模式）；
- 下载解析：校验 token（即焚）后返回附件文件路径。
"""

import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.session_attachment import SessionAttachment

logger = logging.getLogger(__name__)

#: mime 白名单（image + zip，P2 起含压缩包）
_ALLOWED_KINDS: dict[str, tuple[str, ...]] = {
    "image": ("image/png", "image/jpeg", "image/webp", "image/gif"),
    # 压缩包白名单：zip/rar/7z/tar 系全支持；浏览器对部分格式误报
    # application/octet-stream，故另有扩展名兜底判定（_resolve_kind）
    "archive": (
        "application/zip",
        "application/x-zip-compressed",
        "application/vnd.rar",
        "application/x-rar-compressed",
        "application/x-7z-compressed",
        "application/gzip",
        "application/x-gzip",
        "application/x-bzip2",
        "application/x-xz",
        "application/x-tar",
    ),
}

#: 压缩包扩展名兜底白名单（mime 不可靠时按文件名后缀判定；含双后缀末段）
_ARCHIVE_EXTENSIONS: set[str] = {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "txz"}
#: 图片扩展名兜底白名单
_IMAGE_EXTENSIONS: set[str] = {"png", "jpg", "jpeg", "webp", "gif"}

#: mime → 扩展名（落盘后缀固定化，防文件名注入）
_MIME_EXTENSIONS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "application/zip": "zip",
    "application/x-zip-compressed": "zip",
    "application/vnd.rar": "rar",
    "application/x-rar-compressed": "rar",
    "application/x-7z-compressed": "7z",
    "application/gzip": "gz",
    "application/x-gzip": "gz",
    "application/x-bzip2": "bz2",
    "application/x-xz": "xz",
    "application/x-tar": "tar",
}

#: 各类别单文件大小上限（ADR-039 P2）：图片 10MB、压缩包 200MB
MAX_SIZE_BY_KIND: dict[str, int] = {
    "image": 10 * 1024 * 1024,
    "archive": 200 * 1024 * 1024,
}
#: 图片类会话总量上限（50MB，ADR-039 P2；压缩包仅受单文件 200MB 限制）
MAX_IMAGE_TOTAL_BYTES = 50 * 1024 * 1024
#: 兼容别名：图片单文件上限（既有测试与文档引用）
MAX_SINGLE_FILE_BYTES = MAX_SIZE_BY_KIND["image"]
MAX_SESSION_TOTAL_BYTES = MAX_IMAGE_TOTAL_BYTES
#: 上传分块大小
_UPLOAD_CHUNK_BYTES = 1024 * 1024

#: 附件类别（供派发/前端判定）
KIND_IMAGE = "image"
KIND_ARCHIVE = "archive"


class AttachmentValidationError(Exception):
    """附件上传校验失败（白名单/大小）。"""


class AttachmentNotFoundError(Exception):
    """附件不存在。"""


@dataclass
class _TokenEntry:
    """一次性下载注册表条目。"""

    path: Path
    expires_at: float
    task_id: str
    attachment_id: str


# 进程内一次性下载注册表（单实例部署；token 即用即焚 + 惰性过期清理）
_token_registry: dict[str, _TokenEntry] = {}


def _storage_dir() -> Path:
    return Path(get_settings().attachment_storage_dir)


def _purge_expired() -> None:
    """惰性清理过期 token 注册。"""
    now = time.monotonic()
    for token in [t for t, e in _token_registry.items() if e.expires_at < now]:
        _token_registry.pop(token, None)


class AttachmentService:
    """会话附件服务。"""

    async def save_upload(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
        upload: UploadFile,
    ) -> SessionAttachment:
        """保存上传附件：校验白名单与大小后落盘落库。

        Raises:
            AttachmentValidationError: MIME 不在白名单、超出单文件/会话总量限制。
        """
        mime = (upload.content_type or "").split(";")[0].strip().lower()
        kind = self._resolve_kind(mime, upload.filename)
        if kind is None:
            raise AttachmentValidationError(
                f"不支持的附件类型：{mime or '未知'}"
                "（仅支持 png/jpg/webp/gif 图片与压缩包 "
                "zip/rar/7z/tar.gz/tgz/tar.bz2/tar.xz/tar）"
            )
        max_single = MAX_SIZE_BY_KIND[kind]

        raw_name = Path(upload.filename or "unnamed").name  # basename 清洗防路径穿越
        file_name = raw_name[:255] or "unnamed"
        # 落盘后缀：扩展名优先（处理浏览器 mime 误报），无扩展名按 mime 推断
        ext = Path(file_name).suffix.lstrip(".").lower()
        if not ext:
            ext = _MIME_EXTENSIONS.get(mime, "bin")

        if kind == "image":
            # 图片共享会话总量配额（50MB）；压缩包仅受单文件限制（ADR-039 P2）
            total_result = await session.execute(
                select(func.coalesce(func.sum(SessionAttachment.size), 0)).where(
                    SessionAttachment.session_id == session_id,
                    SessionAttachment.kind == "image",
                )
            )
            image_total = int(total_result.scalar_one())
            declared = upload.size or 0
            if image_total + declared > MAX_IMAGE_TOTAL_BYTES:
                raise AttachmentValidationError("会话图片附件总量超出 50MB 上限")
        else:
            declared = upload.size or 0
        # 预校验按声明大小（读取时仍逐块限流兜底）
        if declared > max_single:
            limit_mb = max_single // (1024 * 1024)
            raise AttachmentValidationError(
                f"附件过大：{file_name}（{declared} 字节），单文件上限 {limit_mb}MB"
            )

        attachment_id = uuid.uuid4()
        rel_path = f"{session_id}/{attachment_id}.{ext}"
        dest = _storage_dir() / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)

        written = 0
        try:
            with dest.open("wb") as out:
                while chunk := await upload.read(_UPLOAD_CHUNK_BYTES):
                    written += len(chunk)
                    if written > max_single:
                        limit_mb = max_single // (1024 * 1024)
                        raise AttachmentValidationError(
                            f"附件过大：{file_name}，单文件上限 {limit_mb}MB"
                        )
                    out.write(chunk)
        except Exception:
            dest.unlink(missing_ok=True)
            raise

        record = SessionAttachment(
            id=attachment_id,
            session_id=session_id,
            task_id=None,
            file_name=file_name,
            mime=mime,
            size=written,
            kind=kind,
            storage_path=rel_path,
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        logger.info(
            "attachment_uploaded",
            extra={
                "event": "attachment_uploaded",
                "attachment_id": str(attachment_id),
                "session_id": str(session_id),
                "kind": kind,
                "mime": mime,
                "size": written,
            },
        )
        return record

    async def list_by_session(
        self, session: AsyncSession, session_id: uuid.UUID
    ) -> list[SessionAttachment]:
        """查询会话全部附件。"""
        result = await session.execute(
            select(SessionAttachment)
            .where(SessionAttachment.session_id == session_id)
            .order_by(SessionAttachment.created_at.asc())
        )
        return list(result.scalars().all())

    async def get(self, session: AsyncSession, attachment_id: uuid.UUID) -> SessionAttachment:
        """按 ID 查询附件。

        Raises:
            AttachmentNotFoundError: 附件不存在。
        """
        record = await session.get(SessionAttachment, attachment_id)
        if record is None:
            raise AttachmentNotFoundError(f"附件不存在：{attachment_id}")
        return record

    async def attach_to_task(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
        task_id: uuid.UUID,
    ) -> int:
        """把会话下尚未关联任务的附件回填 task_id，返回回填数量。"""
        result = await session.execute(
            select(SessionAttachment).where(
                SessionAttachment.session_id == session_id,
                SessionAttachment.task_id.is_(None),
            )
        )
        records = list(result.scalars().all())
        for record in records:
            record.task_id = task_id
        if records:
            await session.commit()
        logger.info(
            "attachments_attached_to_task",
            extra={
                "event": "attachments_attached_to_task",
                "session_id": str(session_id),
                "task_id": str(task_id),
                "count": len(records),
            },
        )
        return len(records)

    async def issue_task_payload(self, session: AsyncSession, task_id: uuid.UUID) -> list[dict]:
        """任务派发前构造 attachments payload：逐附件签发一次性下载 token。

        Returns:
            ``[{id, file_name, mime, size, url}]``；任务无附件时返回空列表。
        """
        _purge_expired()
        result = await session.execute(
            select(SessionAttachment)
            .where(SessionAttachment.task_id == task_id)
            .order_by(SessionAttachment.created_at.asc())
        )
        records = list(result.scalars().all())
        payload: list[dict] = []
        for record in records:
            path = _storage_dir() / record.storage_path
            if not path.is_file():
                logger.warning(
                    "attachment_file_missing",
                    extra={
                        "event": "attachment_file_missing",
                        "attachment_id": str(record.id),
                        "task_id": str(task_id),
                    },
                )
                continue
            token = secrets.token_urlsafe(32)
            _token_registry[token] = _TokenEntry(
                path=path,
                expires_at=time.monotonic() + get_settings().attachment_token_ttl_seconds,
                task_id=str(task_id),
                attachment_id=str(record.id),
            )
            payload.append(
                {
                    "id": str(record.id),
                    "file_name": record.file_name,
                    "mime": record.mime,
                    "size": record.size,
                    "url": (f"/api/v1/tasks/{task_id}/attachments/{record.id}?token={token}"),
                }
            )
        if payload:
            logger.info(
                "attachment_tokens_issued",
                extra={
                    "event": "attachment_tokens_issued",
                    "task_id": str(task_id),
                    "count": len(payload),
                    "total_size": sum(item["size"] for item in payload),
                },
            )
        return payload

    def resolve_download(self, task_id: str, attachment_id: str, token: str) -> Path | None:
        """校验一次性下载 token，返回附件文件路径；无效/过期/错配返回 None。"""
        _purge_expired()
        entry = _token_registry.get(token)
        if entry is None:
            return None
        if entry.task_id != task_id or entry.attachment_id != attachment_id:
            return None
        _token_registry.pop(token, None)  # 一次性：下载即焚
        if not entry.path.is_file():
            return None
        return entry.path

    @staticmethod
    def _resolve_kind(mime: str, filename: str | None = None) -> str | None:
        """推断附件类别：MIME 白名单优先，扩展名兜底（浏览器 mime 不可靠）。

        压缩包全格式支持（zip/rar/7z/tar.gz/tgz/tar.bz2/tar.xz/tar）；
        不在白名单返回 None。
        """
        ext = Path(filename or "").suffix.lstrip(".").lower()
        if mime in _ALLOWED_KINDS["image"] or ext in _IMAGE_EXTENSIONS:
            return "image"
        if mime in _ALLOWED_KINDS["archive"] or ext in _ARCHIVE_EXTENSIONS:
            return "archive"
        return None
