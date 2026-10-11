"""诊断：Upload 调用的 result（hub_note 是否注入）与 Bash 返回。"""

import asyncio
import sys

sys.path.insert(0, ".")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings


async def main() -> None:
    engine = create_async_engine(get_settings().database_url, echo=False)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT kind, count(*) FROM tool_call_logs "
                    "GROUP BY kind ORDER BY kind"
                )
            )
        ).fetchall()
        for row in rows:
            print("kind 分布:", row[0], "=", row[1])
        bt = (
            await conn.execute(
                text(
                    "SELECT created_at, name, status FROM tool_call_logs "
                    "WHERE kind='bt_mcp_call' ORDER BY created_at DESC LIMIT 5"
                )
            )
        ).fetchall()
        for row in bt:
            print("bt_mcp_call:", row)
    await engine.dispose()


asyncio.run(main())
