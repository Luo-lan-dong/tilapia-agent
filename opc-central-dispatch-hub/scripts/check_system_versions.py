"""临时排查脚本：检查 system_versions 表状态（2026-09-09 更新日志为空问题）。"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.models.base import AsyncSessionLocal


async def main() -> None:
    async with AsyncSessionLocal() as session:
        # 1. 表是否存在
        exists = await session.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables"
                "  WHERE table_name = 'system_versions'"
                ")"
            )
        )
        print("system_versions 表存在:", exists.scalar())

        # 2. 行数与内容
        rows = await session.execute(
            text(
                "SELECT id, version, title, release_date, is_current, created_at"
                " FROM system_versions ORDER BY release_date DESC"
            )
        )
        records = rows.fetchall()
        print("system_versions 行数:", len(records))
        for r in records:
            print(" ", tuple(r))

        # 3. alembic 当前版本（判断库是否被重建/回滚）
        try:
            ver = await session.execute(text("SELECT version_num FROM alembic_version"))
            print("alembic_version:", ver.scalar())
        except Exception as exc:  # noqa: BLE001
            print("alembic_version 查询失败:", exc)

        # 4. 其他核心表行数（判断库是否被整体重建过）
        for table in ("agent_nodes", "tasks", "system_settings"):
            try:
                cnt = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))  # noqa: S608
                print(f"{table} 行数:", cnt.scalar())
            except Exception as exc:  # noqa: BLE001
                print(f"{table} 查询失败:", exc)


if __name__ == "__main__":
    asyncio.run(main())
