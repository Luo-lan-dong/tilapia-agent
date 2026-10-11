"""经验记忆服务测试（ADR-050）。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experience_record import ExperienceRecord
from app.services.experience_service import ExperienceService, normalize_signature

_SERVICE = ExperienceService()


@pytest.fixture(autouse=True)
async def _clean_records(db_session: AsyncSession):
    """每个用例前后清空 experience_records。"""
    await db_session.execute(delete(ExperienceRecord))
    await db_session.commit()
    yield
    await db_session.execute(delete(ExperienceRecord))
    await db_session.commit()


async def _seed(db_session: AsyncSession, **overrides) -> ExperienceRecord:
    """写入一条经验条目（overrides 覆盖默认字段）。"""
    fields: dict = {
        "tool": "HtmlProjectInfo",
        "error_signature": "未找到该网站",
        "problem": "工具 HtmlProjectInfo 调用失败：未找到该网站",
        "status": "pending",
    }
    fields.update(overrides)
    record = ExperienceRecord(**fields)
    db_session.add(record)
    await db_session.commit()
    await db_session.refresh(record)
    return record


def test_normalize_signature_strips_volatile_parts() -> None:
    """UT-E2：去数字/压空白归一化（IP/端口/耗时波动不 signature 失配）。"""
    first = normalize_signature("错误: 未找到该网站 192.168.101.13")
    second = normalize_signature("错误:  未找到该网站 10.0.0.1")
    assert first == second
    assert "192" not in first
    assert normalize_signature("") == ""


async def test_record_failure_persists_pending(db_session: AsyncSession) -> None:
    """UT-E1：失败记录落库，字段齐备。"""
    record = await _SERVICE.record_failure(
        db_session,
        tool="SiteDelete",
        error_msg="站点不存在 x",
        args_summary='{"site_name": "x"}',
        session_id="sess-abc",
    )
    assert record is not None
    assert record.status == "pending"
    assert record.tool == "SiteDelete"
    assert record.session_id == "sess-abc"
    assert "站点不存在" in (record.problem or "")
    assert "site_name" in (record.problem or "")


async def test_record_failure_dedup_same_session(db_session: AsyncSession) -> None:
    """UT-E8：同 session+tool+签名已有 pending 时不重复写。"""
    await _SERVICE.record_failure(db_session, tool="T", error_msg="boom 1", session_id="s1")
    again = await _SERVICE.record_failure(db_session, tool="T", error_msg="boom 2", session_id="s1")
    assert again is None


async def test_find_matches_resolved_first(db_session: AsyncSession) -> None:
    """UT-E3：签名匹配，resolved 优先于 pending。"""
    raw_msg = "未找到该网站 1.2.3.4"
    normalized = normalize_signature(raw_msg)
    await _seed(db_session, error_signature=normalized, status="pending")
    resolved = await _seed(
        db_session,
        error_signature=normalized,
        status="resolved",
        root_cause="静态站",
        solution="用 Html 族",
        hit_count=2,
    )
    matches = await _SERVICE.find_matches(db_session, signature=raw_msg)
    assert matches
    assert matches[0].id == resolved.id


async def test_resolve_backfills(db_session: AsyncSession) -> None:
    """UT-E4：回填 root_cause/solution，status=resolved，resolved_at 写入。"""
    record = await _seed(db_session)
    updated = await _SERVICE.resolve(db_session, record.id, root_cause="根因 A", solution="解法 B")
    assert updated is not None
    assert updated.status == "resolved"
    assert updated.root_cause == "根因 A"
    assert updated.solution == "解法 B"
    assert updated.resolved_at is not None


async def test_resolve_missing_returns_none(db_session: AsyncSession) -> None:
    """UT-E4b：不存在的条目回填返回 None。"""
    assert await _SERVICE.resolve(db_session, uuid.uuid4(), root_cause="r", solution="s") is None


async def test_list_keyword_and_status_filter(db_session: AsyncSession) -> None:
    """UT-E5：keyword 模糊 + status 过滤 + 分页。"""
    await _seed(db_session, tool="HtmlProjectInfo")
    await _seed(db_session, tool="SiteDelete", error_signature="端口不合法")
    records, total = await _SERVICE.list_records(
        db_session, keyword="端口", status_filter="pending"
    )
    assert total == 1
    assert records[0].tool == "SiteDelete"
    all_records, all_total = await _SERVICE.list_records(db_session, page=1, page_size=1)
    assert all_total == 2
    assert len(all_records) == 1


async def test_list_session_pending(db_session: AsyncSession) -> None:
    """收尾总结钩子数据源：仅返回指定会话的 pending 条目。"""
    await _seed(db_session, session_id="sess-1")
    await _seed(db_session, session_id="sess-1", status="resolved")
    await _seed(db_session, session_id="sess-2")
    pending = await _SERVICE.list_session_pending(db_session, "sess-1")
    assert len(pending) == 1
    assert await _SERVICE.list_session_pending(db_session, None) == []


async def test_delete_record(db_session: AsyncSession) -> None:
    """UT-E6：删除后查询不存在。"""
    record = await _seed(db_session)
    await _SERVICE.delete_record(db_session, record)
    assert await _SERVICE.get_record(db_session, record.id) is None
