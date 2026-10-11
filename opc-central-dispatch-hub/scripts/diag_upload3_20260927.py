"""诊断：Upload 调用的完整记录（hub_note / next_action / 失败原因）。"""

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
                    "SELECT created_at, args_summary, result_summary, error "
                    "FROM tool_call_logs WHERE name='Upload' AND kind='bt_mcp_call' "
                    "ORDER BY created_at DESC LIMIT 4"
                )
            )
        ).fetchall()
        if not rows:
            print("!! 无 kind=bt_mcp_call name=Upload 记录（审计仍被吞或 AI 未真调）")
        for row in rows:
            print("=====", row.created_at)
            print("args:", str(row.args_summary)[:250])
            print("result:", str(row.result_summary)[:1200])
            if row.error:
                print("error:", str(row.error)[:300])
    await engine.dispose()


asyncio.run(main())
