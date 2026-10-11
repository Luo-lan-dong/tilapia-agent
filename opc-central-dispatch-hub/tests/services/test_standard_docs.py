"""开发规则文档服务测试（ADR-048）。"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.standard_doc import StandardDoc
from app.models.task import Task
from app.schemas.standard_doc import StandardDocCreate, StandardDocUpdate
from app.services.standard_doc_service import (
    StandardDocService,
    build_rule_docs_payload,
    resolve_rule_doc_download,
    sanitize_file_name,
)


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """清理文档与任务表（保留预置模板，防止清掉迁移 seed）。"""
    await db_session.execute(delete(StandardDoc).where(StandardDoc.is_builtin.is_(False)))
    await db_session.execute(delete(Task))
    yield


def _make_doc(name: str = "test-doc", content: str = "# 规范正文") -> StandardDocCreate:
    return StandardDocCreate(
        name=name,
        category="coding-standard",
        description="测试文档",
        apply_when="每次写/改代码前",
        content=content,
    )


async def test_create_update_delete_doc(db_session: AsyncSession) -> None:
    """CRUD 全流程：创建 version=1，更新自增，删除生效。"""
    service = StandardDocService()
    doc = await service.create_doc(db_session, _make_doc())
    assert doc.version == 1
    assert doc.is_builtin is False

    updated = await service.update_doc(db_session, doc, StandardDocUpdate(content="# 新正文"))
    assert updated.version == 2
    assert updated.content == "# 新正文"

    await service.delete_doc(db_session, updated)
    assert await service.get_doc(db_session, updated.id) is None


async def test_create_duplicate_name_conflict(db_session: AsyncSession) -> None:
    """同名创建返回 409（业务码 206002）。"""
    from fastapi import status

    from app.core.errors import BusinessError, ErrorCode

    service = StandardDocService()
    await service.create_doc(db_session, _make_doc())
    with pytest.raises(BusinessError) as exc_info:
        await service.create_doc(db_session, _make_doc())
    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert exc_info.value.biz_code == ErrorCode.DOC_NAME_DUPLICATE


def test_sanitize_file_name() -> None:
    """文件名净化：中文名保留、非法字符替换、空串回退随机名。"""
    assert sanitize_file_name("coding-standards") == "coding-standards"
    assert sanitize_file_name("数据库 规范/v1") == "数据库_规范_v1"
    fallback = sanitize_file_name("../../etc")
    assert "/" not in fallback and "\\" not in fallback and ".." not in fallback
    assert sanitize_file_name("///") != ""


async def test_snapshot_rule_docs_locks_content(db_session: AsyncSession) -> None:
    """快照语义：创建快照后文档迭代/删除，快照内容不变。"""
    service = StandardDocService()
    doc = await service.create_doc(db_session, _make_doc(content="# v1 正文"))

    snapshots = await service.snapshot_rule_docs(db_session, [doc.id])
    assert snapshots is not None and len(snapshots) == 1
    assert snapshots[0]["content"] == "# v1 正文"
    assert snapshots[0]["version"] == 1

    # 文档迭代到 v2 并删除，快照仍为 v1 内容
    await service.update_doc(db_session, doc, StandardDocUpdate(content="# v2 正文"))
    await service.delete_doc(db_session, doc)
    assert snapshots[0]["content"] == "# v1 正文"


async def test_snapshot_missing_doc_raises(db_session: AsyncSession) -> None:
    """快照时文档 ID 不存在返回 404（业务码 206001）。"""
    from fastapi import status

    from app.core.errors import BusinessError, ErrorCode

    service = StandardDocService()
    with pytest.raises(BusinessError) as exc_info:
        await service.snapshot_rule_docs(db_session, [uuid.uuid4()])
    assert exc_info.value.biz_code == ErrorCode.DOC_NOT_FOUND
    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


async def test_snapshot_empty_ids_returns_none(db_session: AsyncSession) -> None:
    """空 ID 列表返回 None（任务不绑定规则包）。"""
    service = StandardDocService()
    assert await service.snapshot_rule_docs(db_session, []) is None


async def test_snapshot_filters_meta_rule_docs(db_session: AsyncSession) -> None:
    """元规则文档误选为规范正文时被过滤（元规则只走派发自动附加通道）。"""
    service = StandardDocService()
    meta = (
        await db_session.execute(
            select(StandardDoc).where(StandardDoc.name == "coding-standards")
        )
    ).scalar_one()  # 迁移 seed 的内置元规则
    assert meta.category == "meta-rule"
    snapshots = await service.snapshot_rule_docs(db_session, [meta.id])
    assert snapshots is None


async def test_build_payload_and_download_roundtrip(db_session: AsyncSession) -> None:
    """payload 构造与下载闭环：规范正文+元规则自动附加（清单渲染）+token 下载。"""
    service = StandardDocService()
    doc = await service.create_doc(db_session, _make_doc(content="# 规则内容"))
    snapshots = await service.snapshot_rule_docs(db_session, [doc.id])

    task = Task(
        title="测试任务",
        agent_type="code",
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
        rule_docs=snapshots,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    payload = await build_rule_docs_payload(db_session, task)
    # 规范正文 1 份 + 自动附加元规则 2 份
    assert payload is not None and len(payload) == 3

    standard = payload[0]
    content_bytes = "# 规则内容".encode()
    assert standard["target"] == "standard"
    assert standard["file_name"] == "test-doc"
    assert standard["sha256"] == hashlib.sha256(content_bytes).hexdigest()
    assert standard["size"] == len(content_bytes)
    assert standard["url"].startswith("/api/v1/docs/download/rule-doc?token=")

    meta_rules = {entry["name"]: entry for entry in payload[1:]}
    assert set(meta_rules) == {"coding-standards", "decision-documents"}
    assert all(entry["target"] == "meta-rule" for entry in meta_rules.values())
    # 元规则①清单渲染：{{STANDARDS_TABLE}} 已替换为本次下发的规范清单
    coding_token = meta_rules["coding-standards"]["url"].split("token=")[1]
    _, coding_content = resolve_rule_doc_download(coding_token)
    assert b"{{STANDARDS_TABLE}}" not in coding_content
    coding_text = coding_content.decode("utf-8")
    assert "docs/standards/test-doc.md" in coding_text
    assert "每次写/改代码前" in coding_text
    # 元规则②原样（含决策文档体系路径指向）
    _, dd_content = resolve_rule_doc_download(
        meta_rules["decision-documents"]["url"].split("token=")[1]
    )
    assert b"docs/INDEX.md" in dd_content

    token = standard["url"].split("token=")[1]
    file_name, resolved = resolve_rule_doc_download(token)
    assert file_name == "test-doc"
    assert resolved == content_bytes
    # TTL 内可重复下载（支持 Agent 重试）
    assert resolve_rule_doc_download(token) == (file_name, resolved)


async def test_build_payload_appends_environment_check_doc(
    db_session: AsyncSession,
) -> None:
    """环境清单非空时自动附加 environment-check.md（清单渲染，target=meta-rule）。"""
    task = Task(
        title="环境检查文档任务",
        agent_type="code",
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
        environment_requirements=[
            {"name": "Python", "check_cmd": "python3 --version", "min_version": "3.10"}
        ],
    )
    payload = await build_rule_docs_payload(db_session, task)
    assert payload is not None
    # 元规则 2 份 + 环境检查文档 1 份
    assert len(payload) == 3
    env_doc = next(entry for entry in payload if entry["name"] == "environment-check")
    assert env_doc["target"] == "meta-rule"
    token = env_doc["url"].split("token=")[1]
    _, content = resolve_rule_doc_download(token)
    text = content.decode("utf-8")
    assert "{{ENV_TABLE}}" not in text
    assert "python3 --version" in text
    assert "3.10" in text
    assert ".tilacode/environment.md" in text


async def test_build_payload_none_without_rule_docs(db_session: AsyncSession) -> None:
    """未绑定规则包且无环境清单的任务返回 None，payload 不带 rule_docs 字段。"""
    task = Task(
        title="无规则任务",
        agent_type="code",
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
    )
    assert await build_rule_docs_payload(db_session, task) is None
