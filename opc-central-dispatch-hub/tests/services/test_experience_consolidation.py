"""经验定时整理测试（ADR-061 M6）：解析容错、草稿态、不污染召回、确认后生效。

覆盖用户已定语义：定时整理**只产出草稿**（`status="draft"`），不直接生效；
草稿**不会**被派发前召回（召回只认 `resolved`）；人工确认（复用 `resolve`）后才生效。
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.experience_consolidator import ExperienceConsolidator, parse_draft
from app.models.experience_record import ExperienceRecord
from app.services.experience_service import ExperienceService

_ENV = "env-test"
_SERVICE = ExperienceService()


def test_parse_draft_two_sections() -> None:
    """UT-M6-10：解析「根因/解法」两段；多行解法合并为一句；缺段返回 None。"""
    parsed = parse_draft("根因：沙箱禁止 spawn\n解法：改用文件重定向\n并复用已有进程")
    assert parsed is not None
    assert parsed[0] == "沙箱禁止 spawn"
    assert parsed[1] == "改用文件重定向 并复用已有进程"

    assert parse_draft("根因：只有这一段") is None
    assert parse_draft("") is None


@pytest.fixture(autouse=True)
async def _clean_records(db_session: AsyncSession):
    """每个用例前后清空经验表。"""
    await db_session.execute(delete(ExperienceRecord))
    await db_session.commit()
    yield
    await db_session.execute(delete(ExperienceRecord))
    await db_session.commit()


async def _pending(db_session: AsyncSession, signature: str) -> ExperienceRecord:
    """写入一条 pending 经验（现象快照）。"""
    record = ExperienceRecord(
        tool="bash",
        error_signature=signature,
        problem="命令报 EPERM",
        status="pending",
        session_id=str(uuid.uuid4()),
        source_node_id="node-1",
        environment_signature=_ENV,
    )
    db_session.add(record)
    await db_session.commit()
    await db_session.refresh(record)
    return record


async def test_consolidate_writes_draft_only(db_session: AsyncSession) -> None:
    """UT-M6-11：整理后状态为 **draft**（不生效），且召回**看不到**它。"""
    record = await _pending(db_session, "sig-1")
    client = AsyncMock()
    client.chat_completion = AsyncMock(return_value="根因：沙箱禁止 spawn\n解法：改文件重定向")

    result = await ExperienceConsolidator(client=client).consolidate(db_session)

    assert result == {"candidates": 1, "drafted": 1, "failed": 0}
    await db_session.refresh(record)
    assert record.status == "draft"
    assert record.root_cause == "沙箱禁止 spawn"
    assert record.solution == "改文件重定向"

    # 草稿不得进入派发前召回（否则未确认的猜测会污染子 Agent）
    recalled = await _SERVICE.recall_for_dispatch(db_session, environment_signature=_ENV)
    assert recalled == []


async def test_consolidate_keeps_pending_on_llm_failure(db_session: AsyncSession) -> None:
    """UT-M6-12：LLM 失败/解析失败 → 保持 pending（下次再试），不写半成品草稿。"""
    record = await _pending(db_session, "sig-2")
    client = AsyncMock()
    client.chat_completion = AsyncMock(return_value="这句没有分段，解析不出结论")

    result = await ExperienceConsolidator(client=client).consolidate(db_session)

    assert result == {"candidates": 1, "drafted": 0, "failed": 1}
    await db_session.refresh(record)
    assert record.status == "pending"
    assert record.root_cause is None


async def test_confirm_draft_makes_it_effective(db_session: AsyncSession) -> None:
    """UT-M6-13：人工确认（复用 resolve）→ resolved 后才进入派发前召回。"""
    record = await _pending(db_session, "sig-3")
    client = AsyncMock()
    client.chat_completion = AsyncMock(return_value="根因：端口被占用\n解法：换端口或释放占用")
    await ExperienceConsolidator(client=client).consolidate(db_session)

    confirmed = await _SERVICE.resolve(
        db_session,
        str(record.id),
        root_cause="端口被占用（已确认）",
        solution="换端口或释放占用",
    )

    assert confirmed is not None and confirmed.status == "resolved"
    recalled = await _SERVICE.recall_for_dispatch(db_session, environment_signature=_ENV)
    assert [item.id for item in recalled] == [record.id]
