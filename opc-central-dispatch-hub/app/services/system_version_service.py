"""系统版本服务层。"""

import logging

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_version import SystemVersion

logger = logging.getLogger(__name__)


class SystemVersionService:
    """系统版本管理服务。

    MVP 阶段仅提供只读查询，版本数据通过 Python 脚本维护（见 ADR-018）。
    """

    async def list_versions(self, session: AsyncSession) -> list[SystemVersion]:
        """查询所有版本记录，按发布日期倒序、创建时间倒序排列。

        Args:
            session: 异步数据库会话。

        Returns:
            版本记录列表。
        """
        stmt: Select[tuple[SystemVersion]] = select(SystemVersion).order_by(
            SystemVersion.release_date.desc(), SystemVersion.created_at.desc()
        )
        result = await session.execute(stmt)
        versions = list(result.scalars().all())

        logger.debug(
            "system_versions_queried",
            extra={"event": "system_versions_queried", "count": len(versions)},
        )
        return versions
