"""SOP 知识服务（ADR-051）。

builtin 场景要点与对话固化（learned）SOP 的存储/命中/管理。
命中即累计 hit_count；同名拒绝（唯一约束的业务化表达）。
"""

import logging

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.models.sop_record import SopRecord

logger = logging.getLogger(__name__)


class SopService:
    """SOP 条目的命中、固化与管理。"""

    async def find_by_scene(self, session: AsyncSession, scene: str) -> SopRecord | None:
        """按场景命中启用的 SOP（updated_at 最新优先），命中累计 hit_count。"""
        result = await session.execute(
            select(SopRecord)
            .where(SopRecord.scene == scene.strip().lower(), SopRecord.enabled.is_(True))
            .order_by(SopRecord.updated_at.desc())
            .limit(1)
        )
        record = result.scalar_one_or_none()
        if record is not None:
            record.hit_count = (record.hit_count or 0) + 1
            await session.commit()
        return record

    async def save(
        self,
        session: AsyncSession,
        *,
        name: str,
        scene: str,
        content: str,
        source: str = "learned",
    ) -> SopRecord:
        """固化/创建 SOP（name 唯一，冲突抛 209002）。"""
        name = name.strip()[:100]
        scene = scene.strip().lower()[:30]
        dup = await session.execute(select(SopRecord).where(SopRecord.name == name))
        if dup.scalar_one_or_none() is not None:
            raise BusinessError(
                status_code=409,
                biz_code=ErrorCode.SOP_NAME_DUPLICATE,
                detail=f"同名 SOP 已存在：{name}（可在记忆页编辑或换名）",
            )
        record = SopRecord(name=name, scene=scene, content=content, source=source)
        session.add(record)
        await session.commit()
        await session.refresh(record)
        logger.info(
            "sop_saved",
            extra={
                "event": "sop_saved",
                "sop_id": str(record.id),
                "scene": scene,
                "source": source,
            },
        )
        return record

    async def get_record(self, session: AsyncSession, record_id) -> SopRecord | None:
        """按 ID 查询。"""
        result = await session.execute(select(SopRecord).where(SopRecord.id == record_id))
        return result.scalar_one_or_none()

    async def list_records(
        self,
        session: AsyncSession,
        *,
        keyword: str | None = None,
        scene: str | None = None,
        source: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[SopRecord], int]:
        """列表：keyword 模糊（name/content/scene）+ scene/source 过滤 + 分页。"""
        stmt = select(SopRecord)
        if keyword:
            pattern = f"%{keyword}%"
            stmt = stmt.where(
                or_(
                    SopRecord.name.ilike(pattern),
                    SopRecord.scene.ilike(pattern),
                    SopRecord.content.ilike(pattern),
                )
            )
        if scene:
            stmt = stmt.where(SopRecord.scene == scene.strip().lower())
        if source:
            stmt = stmt.where(SopRecord.source == source)
        total_result = await session.execute(select(func.count()).select_from(stmt.subquery()))
        total = int(total_result.scalar_one())
        result = await session.execute(
            stmt.order_by(SopRecord.updated_at.desc())
            .offset(max(page - 1, 0) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def delete_record(self, session: AsyncSession, record: SopRecord) -> None:
        """删除条目。"""
        await session.delete(record)
        await session.commit()
        logger.info(
            "sop_deleted",
            extra={"event": "sop_deleted", "sop_id": str(record.id)},
        )
