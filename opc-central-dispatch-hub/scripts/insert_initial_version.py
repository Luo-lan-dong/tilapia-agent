"""设置中枢当前运行版本（ADR-018 修订 / ADR-026）。

system_versions 自 ADR-026 起仅承载「当前运行版本号」（单条 is_current 记录），
历史更新日志完全云端化（云端资源仓库 hub_releases）。本脚本幂等：
目标版本已存在则置为当前版本，否则插入；同事务将其他记录 is_current 置 false。

用法：
    cd opc-central-dispatch-hub
    python scripts\\insert_initial_version.py [版本号]   # 缺省 v0.5.0
"""

import asyncio
import datetime
import sys
from pathlib import Path

# 将后端根目录加入 sys.path，便于在项目根目录下直接执行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update

from app.models.base import AsyncSessionLocal
from app.models.system_version import SystemVersion

DEFAULT_VERSION = "v0.5.0"

VERSION_TITLES: dict[str, str] = {
    "v0.5.0": "版本发布管理与中枢更新基线",
}


async def main() -> None:
    """设置当前运行版本（幂等）。"""
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_VERSION
    async with AsyncSessionLocal() as session:
        stmt = select(SystemVersion).where(SystemVersion.version == target)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            existing.is_current = True
            print(f"版本 {target} 已存在，置为当前版本")
        else:
            session.add(
                SystemVersion(
                    version=target,
                    title=VERSION_TITLES.get(target, f"中枢版本 {target}"),
                    content=f"中枢当前运行版本 {target}。历史更新日志请见「更新日志」页（云端下发）。",
                    release_date=datetime.date.today(),
                    is_current=True,
                )
            )
            print(f"已插入当前版本 {target}")
        # 同事务将其他记录置为非当前
        await session.execute(
            update(SystemVersion).where(SystemVersion.version != target).values(is_current=False)
        )
        await session.commit()
        rows = (await session.execute(select(SystemVersion))).scalars().all()
        print("当前 system_versions:", [(r.version, r.is_current) for r in rows])


if __name__ == "__main__":
    asyncio.run(main())
