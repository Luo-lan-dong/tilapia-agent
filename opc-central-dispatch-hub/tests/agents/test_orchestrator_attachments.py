"""附件注入分类测试（ADR-039 修复：zip 等非图片附件不再被识别为图片）。"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.orchestrator import OrchestratorAgent
from app.models.session_attachment import SessionAttachment


def _make_record(kind: str, mime: str, size: int = 72884) -> SessionAttachment:
    """构造未落库的附件记录。"""
    return SessionAttachment(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        file_name="task-88dff577-artifacts.zip" if kind == "archive" else "pic.png",
        mime=mime,
        size=size,
        kind=kind,
        storage_path=f"x/{uuid.uuid4()}.bin",
    )


def _make_agent(records: list[SessionAttachment]) -> OrchestratorAgent:
    """构造 OrchestratorAgent（session.get 按记录 ID 返回）。"""
    by_id = {record.id: record for record in records}
    session = MagicMock()
    session.get = AsyncMock(side_effect=lambda model, pk: by_id.get(pk))
    return OrchestratorAgent(session=session)  # type: ignore[arg-type]


async def test_archive_attachment_described_as_file() -> None:
    """zip 附件注入文件占位（名称/类别/大小/下载地址），不再是图片附件。"""
    record = _make_record("archive", "application/x-zip-compressed")
    agent = _make_agent([record])
    with patch(
        "app.agents.orchestrator.get_settings",
        return_value=MagicMock(hub_public_url="http://192.168.1.5:8000/"),
    ):
        converted, notices = await agent._inject_attachment_images(
            [
                {
                    "role": "user",
                    "content": "我需要部署这个站点",
                    "attachments": [{"id": str(record.id), "file_name": record.file_name}],
                }
            ],
            supports_vision=True,
        )
    assert notices == []
    content = converted[0]["content"]
    # 视觉模型：多模态 parts，文件信息以 text part 注入
    text_part = next(
        part for part in content if part["type"] == "text" and "文件附件" in part["text"]
    )
    assert "task-88dff577-artifacts.zip" in text_part["text"]
    assert "zip 压缩包" in text_part["text"]
    assert (
        f"/api/v1/sessions/{record.session_id}/attachments/{record.id}/content" in text_part["text"]
    )
    assert "curl -L" in text_part["text"]
    assert not any(part["type"] == "image_url" for part in content)
    assert "图片附件" not in text_part["text"]


async def test_image_attachment_still_injected_as_vision_part() -> None:
    """图片附件保持视觉注入（回归）。"""
    record = _make_record("image", "image/png")
    agent = _make_agent([record])
    settings = MagicMock(hub_public_url=None, attachment_storage_dir="nonexistent-dir")
    with patch("app.agents.orchestrator.get_settings", return_value=settings):
        converted, _notices = await agent._inject_attachment_images(
            [
                {
                    "role": "user",
                    "content": "看这张图",
                    "attachments": [{"id": str(record.id), "file_name": record.file_name}],
                }
            ],
            supports_vision=True,
        )
    content = converted[0]["content"]
    assert any(part["type"] == "text" and "看这张图" in part["text"] for part in content)


async def test_mixed_attachments_parts_combined() -> None:
    """混合附件：图片 parts + 文件文本 part 共存。"""
    image_record = _make_record("image", "image/png")
    archive_record = _make_record("archive", "application/zip")
    agent = _make_agent([image_record, archive_record])
    settings = MagicMock(hub_public_url=None, attachment_storage_dir="nonexistent-dir")
    with patch("app.agents.orchestrator.get_settings", return_value=settings):
        converted, notices = await agent._inject_attachment_images(
            [
                {
                    "role": "user",
                    "content": "部署",
                    "attachments": [
                        {"id": str(image_record.id), "file_name": image_record.file_name},
                        {"id": str(archive_record.id), "file_name": archive_record.file_name},
                    ],
                }
            ],
            supports_vision=True,
        )
    content = converted[0]["content"]
    text_parts = [part["text"] for part in content if part["type"] == "text"]
    assert any("文件附件" in text and "artifacts.zip" in text for text in text_parts)
    # 图片文件缺失（storage 目录不存在）→ notice 提示，不中断
    assert any("缺失" in notice for notice in notices)
