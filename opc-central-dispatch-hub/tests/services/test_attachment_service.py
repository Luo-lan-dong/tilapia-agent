"""会话附件服务测试（ADR-039 P1）。

覆盖上传白名单/大小/总量校验、任务关联回填、一次性下载 token
签发与即焚校验（同 ADR-034 handoff 模式）。
"""

import io
import uuid
from pathlib import Path

import pytest
from fastapi import UploadFile
from sqlalchemy import select
from starlette.datastructures import Headers

from app.models.session_attachment import SessionAttachment
from app.services.attachment_service import (
    MAX_IMAGE_TOTAL_BYTES,
    MAX_SIZE_BY_KIND,
    AttachmentNotFoundError,
    AttachmentService,
    AttachmentValidationError,
)


def _make_upload(
    data: bytes,
    filename: str = "screenshot.png",
    content_type: str = "image/png",
) -> UploadFile:
    """构造 FastAPI UploadFile 测试样本。"""
    headers = Headers({"content-type": content_type})
    return UploadFile(
        file=io.BytesIO(data),
        size=len(data),
        filename=filename,
        headers=headers,
    )


@pytest.fixture
def storage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把附件存储目录指到临时目录，避免污染开发数据。"""
    target = tmp_path / "attachments"
    monkeypatch.setattr("app.services.attachment_service._storage_dir", lambda: target)
    return target


async def test_save_upload_rejects_non_image(db_session, storage_dir: Path) -> None:
    """P1 仅放行图片：txt 上传被拒绝且不落盘。"""
    service = AttachmentService()
    with pytest.raises(AttachmentValidationError):
        await service.save_upload(
            db_session,
            uuid.uuid4(),
            _make_upload(b"hello", filename="a.txt", content_type="text/plain"),
        )


async def test_save_upload_rejects_oversize(db_session, storage_dir: Path) -> None:
    """单文件超 10MB（按声明大小）被拒绝。"""
    service = AttachmentService()
    big = _make_upload(b"\0" * (MAX_SIZE_BY_KIND["image"] + 1))
    with pytest.raises(AttachmentValidationError):
        await service.save_upload(db_session, uuid.uuid4(), big)


async def test_save_upload_rejects_oversize_archive(db_session, storage_dir: Path) -> None:
    """zip 超 200MB（按声明大小）被拒绝（P2）。"""
    service = AttachmentService()
    big_zip = _make_upload(
        b"\0" * (MAX_SIZE_BY_KIND["archive"] + 1),
        filename="big.zip",
        content_type="application/zip",
    )
    with pytest.raises(AttachmentValidationError):
        await service.save_upload(db_session, uuid.uuid4(), big_zip)


async def test_save_upload_rejects_session_total(db_session, storage_dir: Path) -> None:
    """图片总量超 50MB 被拒绝：已有 49MB 图片记录后再传 2MB 图片。"""
    service = AttachmentService()
    session_id = uuid.uuid4()
    db_session.add(
        SessionAttachment(
            session_id=session_id,
            file_name="huge.png",
            mime="image/png",
            size=MAX_IMAGE_TOTAL_BYTES - 1024 * 1024,
            kind="image",
            storage_path="stub/old.png",
        )
    )
    await db_session.commit()
    with pytest.raises(AttachmentValidationError):
        await service.save_upload(db_session, session_id, _make_upload(b"\0" * (2 * 1024 * 1024)))


async def test_save_upload_zip_succeeds(db_session, storage_dir: Path) -> None:
    """zip 上传成功：kind=archive，不占用图片总量配额（P2）。"""
    service = AttachmentService()
    session_id = uuid.uuid4()
    db_session.add(
        SessionAttachment(
            session_id=session_id,
            file_name="full.png",
            mime="image/png",
            size=MAX_IMAGE_TOTAL_BYTES,  # 图片配额已占满
            kind="image",
            storage_path="stub/full.png",
        )
    )
    await db_session.commit()

    record = await service.save_upload(
        db_session,
        session_id,
        _make_upload(b"PK-zip-bytes", filename="产品.zip", content_type="application/zip"),
    )

    assert record.kind == "archive"
    assert record.mime == "application/zip"
    assert (storage_dir / record.storage_path).read_bytes() == b"PK-zip-bytes"


async def test_save_upload_archive_formats_accepted(db_session, storage_dir: Path) -> None:
    """压缩包全格式支持：tar.gz/rar/7z 经 mime 或扩展名兜底放行（用户需求）。"""
    service = AttachmentService()
    cases = [
        # (文件名, mime, 预期落盘后缀)
        ("backup.tar.gz", "application/gzip", "gz"),
        ("backup.tgz", "application/octet-stream", "tgz"),  # mime 误报 → 扩展名兜底
        ("source.rar", "application/vnd.rar", "rar"),
        ("source.7z", "application/x-7z-compressed", "7z"),
        ("data.tar", "application/x-tar", "tar"),
        ("pack.tar.bz2", "application/x-bzip2", "bz2"),
    ]
    for index, (filename, mime, expected_ext) in enumerate(cases):
        record = await service.save_upload(
            db_session,
            uuid.uuid4(),
            _make_upload(f"archive-{index}".encode(), filename=filename, content_type=mime),
        )
        assert record.kind == "archive", filename
        assert record.storage_path.endswith(f".{expected_ext}"), filename


async def test_save_upload_octet_stream_unknown_ext_rejected(db_session, storage_dir: Path) -> None:
    """octet-stream 且扩展名不在白名单 → 仍拒绝（防任意文件上传）。"""
    service = AttachmentService()
    with pytest.raises(AttachmentValidationError):
        await service.save_upload(
            db_session,
            uuid.uuid4(),
            _make_upload(b"bin", filename="evil.exe", content_type="application/octet-stream"),
        )


async def test_save_upload_persists_file_and_record(db_session, storage_dir: Path) -> None:
    """正常上传：文件落盘到 {session}/{id}.{ext}，记录入库。"""
    service = AttachmentService()
    session_id = uuid.uuid4()
    data = b"\x89PNG fake image bytes"

    record = await service.save_upload(
        db_session, session_id, _make_upload(data, filename="截图 一.png")
    )

    assert record.kind == "image"
    assert record.size == len(data)
    assert record.mime == "image/png"
    saved = storage_dir / record.storage_path
    assert saved.is_file()
    assert saved.read_bytes() == data
    assert record.storage_path.startswith(f"{session_id}/")


async def test_attach_to_task_backfills_pending_only(db_session) -> None:
    """创建任务时只回填本会话未关联任务的附件。"""
    service = AttachmentService()
    session_id = uuid.uuid4()
    task_id = uuid.uuid4()
    db_session.add_all(
        [
            SessionAttachment(
                session_id=session_id,
                file_name="a.png",
                mime="image/png",
                size=1,
                kind="image",
                storage_path="stub/a.png",
            ),
            SessionAttachment(
                session_id=session_id,
                file_name="b.png",
                mime="image/png",
                size=2,
                kind="image",
                storage_path="stub/b.png",
                task_id=uuid.uuid4(),  # 已关联其他任务，不应被改写
            ),
        ]
    )
    await db_session.commit()

    count = await service.attach_to_task(db_session, session_id, task_id)

    assert count == 1
    rows = list(
        (
            await db_session.execute(
                select(SessionAttachment).where(SessionAttachment.session_id == session_id)
            )
        ).scalars()
    )
    backfilled = [row for row in rows if row.task_id == task_id]
    assert len(backfilled) == 1
    assert backfilled[0].file_name == "a.png"


async def test_issue_payload_and_one_time_download(db_session, storage_dir: Path) -> None:
    """派发 payload 按附件签发一次性 token：下载成功后 token 即焚。"""
    service = AttachmentService()
    session_id = uuid.uuid4()
    task_id = uuid.uuid4()
    record = await service.save_upload(
        db_session, session_id, _make_upload(b"png-bytes", filename="bug.png")
    )
    await service.attach_to_task(db_session, session_id, task_id)

    payload = await service.issue_task_payload(db_session, task_id)

    assert len(payload) == 1
    item = payload[0]
    assert item["file_name"] == "bug.png"
    assert item["mime"] == "image/png"
    assert f"/api/v1/tasks/{task_id}/attachments/{record.id}?token=" in item["url"]

    token = item["url"].split("token=")[1]
    path = service.resolve_download(str(task_id), str(record.id), token)
    assert path is not None and path.read_bytes() == b"png-bytes"
    # 一次性：第二次下载同 token 返回 None
    assert service.resolve_download(str(task_id), str(record.id), token) is None


async def test_resolve_download_rejects_mismatch(db_session, storage_dir: Path) -> None:
    """token 与 task/attachment 错配时拒绝下载。"""
    service = AttachmentService()
    session_id = uuid.uuid4()
    task_id = uuid.uuid4()
    record = await service.save_upload(db_session, session_id, _make_upload(b"x", filename="a.png"))
    await service.attach_to_task(db_session, session_id, task_id)
    payload = await service.issue_task_payload(db_session, task_id)
    token = payload[0]["url"].split("token=")[1]

    other_task = uuid.uuid4()
    assert service.resolve_download(str(other_task), str(record.id), token) is None
    assert service.resolve_download(str(task_id), str(uuid.uuid4()), token) is None


async def test_get_attachment_not_found(db_session) -> None:
    """查询不存在的附件抛 AttachmentNotFoundError。"""
    service = AttachmentService()
    with pytest.raises(AttachmentNotFoundError):
        await service.get(db_session, uuid.uuid4())
