"""SOP 知识服务与固化技能测试（ADR-051）。"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.models.sop_record import SopRecord
from app.services.sop_service import SopService
from app.skills.base import SkillContext
from app.skills.sop_save import SopSaveSkill

_SERVICE = SopService()


@pytest.fixture(autouse=True)
async def _clean_custom_sops(db_session: AsyncSession):
    """清理 learned 条目（保留迁移 builtin seed 供命中用例）。"""
    await db_session.execute(delete(SopRecord).where(SopRecord.source == "learned"))
    await db_session.commit()
    yield
    await db_session.execute(delete(SopRecord).where(SopRecord.source == "learned"))
    await db_session.commit()


async def test_seed_builtin_exists(db_session: AsyncSession) -> None:
    """UT-S1：迁移内置五类场景要点在库。"""
    result = await db_session.execute(select(SopRecord).where(SopRecord.source == "builtin"))
    scenes = {record.scene for record in result.scalars().all()}
    assert scenes == {"python", "nodejs", "go", "html", "php"}


async def test_find_by_scene_hits_and_counts(db_session: AsyncSession) -> None:
    """UT-S2：场景命中并累计 hit_count。"""
    record = await _SERVICE.find_by_scene(db_session, "html")
    assert record is not None
    before = record.hit_count
    again = await _SERVICE.find_by_scene(db_session, "html")
    assert again.hit_count == before + 1


async def test_find_by_scene_no_match(db_session: AsyncSession) -> None:
    """UT-S2b：无该场景 SOP 返回 None（自主编排路径）。"""
    assert await _SERVICE.find_by_scene(db_session, "dotnet") is None


async def test_save_rejects_duplicate_name(db_session: AsyncSession) -> None:
    """UT-S3：同名固化拒绝（209002）。"""
    await _SERVICE.save(db_session, name="FastAPI 部署", scene="python", content="要点")
    with pytest.raises(BusinessError) as exc_info:
        await _SERVICE.save(db_session, name="FastAPI 部署", scene="python", content="另一份")
    assert exc_info.value.biz_code == 209002


async def test_sop_save_skill_success(db_session: AsyncSession) -> None:
    """固化技能：用户确认后入库 learned。"""
    context = SkillContext(
        args={
            "name": "WordPress 部署",
            "scene": "php",
            "content": "1. 解压到站点目录 2. SiteCreate 3. wp-config 写库",
        }
    )
    events = [event async for event in SopSaveSkill().execute(db_session, context)]
    result = events[-1]["data"]
    assert result.success is True
    found = await db_session.execute(select(SopRecord).where(SopRecord.name == "WordPress 部署"))
    record = found.scalar_one()
    assert record.source == "learned"
    assert record.scene == "php"


async def test_sop_save_skill_duplicate(db_session: AsyncSession) -> None:
    """固化技能：同名返回失败指引。"""
    await _SERVICE.save(db_session, name="X 部署", scene="go", content="a")
    context = SkillContext(args={"name": "X 部署", "scene": "go", "content": "b"})
    events = [event async for event in SopSaveSkill().execute(db_session, context)]
    result = events[-1]["data"]
    assert result.success is False
    assert "已存在" in result.summary


async def test_sop_save_skill_missing_args(db_session: AsyncSession) -> None:
    """固化技能：缺参返回指引。"""
    context = SkillContext(args={"name": "只有名字"})
    events = [event async for event in SopSaveSkill().execute(db_session, context)]
    result = events[-1]["data"]
    assert result.success is False
    assert "scene" in result.summary
