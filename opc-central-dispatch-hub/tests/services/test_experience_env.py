"""经验记忆环境签名与派发前召回测试（ADR-061 M6）。

覆盖：失败记录带来源节点与环境签名、按环境召回（同环境优先、无匹配回退环境未知）、
召回计数、以及召回文档渲染三要素（现象/根因/解法）。
"""

import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experience_record import ExperienceRecord
from app.services.experience_service import ExperienceService

_ENV_A = "env-aaa"
_ENV_B = "env-bbb"
_SERVICE = ExperienceService()


@pytest.fixture(autouse=True)
async def _clean_records(db_session: AsyncSession):
    """每个用例前后清空经验表。"""
    await db_session.execute(delete(ExperienceRecord))
    await db_session.commit()
    yield
    await db_session.execute(delete(ExperienceRecord))
    await db_session.commit()


async def _resolved(
    db_session: AsyncSession, *, signature: str, environment: str | None, hits: int = 0
) -> ExperienceRecord:
    """写入一条已总结的经验。"""
    record = ExperienceRecord(
        tool="bash",
        error_signature=signature,
        problem="命令报 EPERM",
        root_cause="沙箱禁止 spawn",
        solution="改用文件重定向或放宽权限",
        status="resolved",
        session_id=str(uuid.uuid4()),
        source_node_id="node-1",
        environment_signature=environment,
        hit_count=hits,
    )
    db_session.add(record)
    await db_session.commit()
    await db_session.refresh(record)
    return record


async def test_record_failure_keeps_source_and_environment(db_session: AsyncSession) -> None:
    """UT-M6-4：失败自动记录带上来源节点与环境签名（跨机溯源与匹配的前提）。"""
    record = await _SERVICE.record_failure(
        db_session,
        tool="bash",
        error_msg="Operation not permitted",
        session_id="s1",
        source_node_id="node-9",
        environment_signature="  env-xyz  ",
    )

    assert record is not None
    assert record.source_node_id == "node-9"
    assert record.environment_signature == "env-xyz"  # 去空白后截断存储


async def test_recall_prefers_same_environment(db_session: AsyncSession) -> None:
    """UT-M6-5：同环境经验优先；异环境经验**不**被召回（避免误用）。"""
    same = await _resolved(db_session, signature="sig-a", environment=_ENV_A)
    await _resolved(db_session, signature="sig-b", environment=_ENV_B)

    records = await _SERVICE.recall_for_dispatch(db_session, environment_signature=_ENV_A)

    assert [record.id for record in records] == [same.id]


async def test_recall_falls_back_to_unknown_environment(db_session: AsyncSession) -> None:
    """UT-M6-6：无同环境命中时回退「环境未知」旧经验（ADR-050 时期条目）；仍不跨环境乱用。"""
    legacy = await _resolved(db_session, signature="sig-legacy", environment=None)
    await _resolved(db_session, signature="sig-b", environment=_ENV_B)

    records = await _SERVICE.recall_for_dispatch(db_session, environment_signature="env-ccc")

    assert [record.id for record in records] == [legacy.id]


async def test_recall_orders_by_hits_then_recency(db_session: AsyncSession) -> None:
    """UT-M6-7：同环境内按命中次数降序（价值优先）。"""
    low = await _resolved(db_session, signature="sig-low", environment=_ENV_A, hits=1)
    high = await _resolved(db_session, signature="sig-high", environment=_ENV_A, hits=9)

    records = await _SERVICE.recall_for_dispatch(db_session, environment_signature=_ENV_A)

    assert [record.id for record in records] == [high.id, low.id]


async def test_record_hits_counts_recall(db_session: AsyncSession) -> None:
    """UT-M6-8：召回即累计命中次数（衡量经验价值）。"""
    record = await _resolved(db_session, signature="sig-a", environment=_ENV_A, hits=2)

    counted = await _SERVICE.record_hits(db_session, [record])

    assert counted == 1
    await db_session.refresh(record)
    assert record.hit_count == 3


async def test_render_dispatch_doc_contains_three_parts(db_session: AsyncSession) -> None:
    """UT-M6-9：召回文档含环境标签与「现象/根因/解法」三要素，并提示与任务无关时忽略。"""
    record = await _resolved(db_session, signature="sig-a", environment=_ENV_A)

    content = _SERVICE.render_dispatch_doc([record], _ENV_A)

    assert _ENV_A in content
    assert "现象：命令报 EPERM" in content
    assert "根因：沙箱禁止 spawn" in content
    assert "解法：改用文件重定向或放宽权限" in content
    assert "忽略即可" in content
