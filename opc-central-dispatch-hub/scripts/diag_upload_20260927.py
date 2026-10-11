"""诊断：Upload 调用的实际返回（hub_note 是否出现）与后端代码版本。"""

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
                    "FROM tool_call_logs WHERE name = 'Upload' "
                    "ORDER BY created_at DESC LIMIT 5"
                )
            )
        ).fetchall()
        for row in rows:
            print("=====", row.created_at)
            print("args:", str(row.args_summary)[:200])
            print("result:", str(row.result_summary)[:800])
            print("error:", str(row.error)[:200])
        count = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM tool_call_logs "
                    "WHERE name='Upload' AND result_summary LIKE '%hub_note%'"
                )
            )
        ).scalar_one()
        print("含 hub_note 的 Upload 记录数：", count)
        skill_rows = (
            await conn.execute(
                text(
                    "SELECT created_at, name, status, result_summary, error "
                    "FROM tool_call_logs WHERE kind IN ('skill_call','skill_result','bt_mcp_call') "
                    "ORDER BY created_at DESC LIMIT 15"
                )
            )
        ).fetchall()
        for row in skill_rows:
            print("[skill]", row.created_at, row.name, row.status, str(row.result_summary)[:120], str(row.error)[:120])
    await engine.dispose()


asyncio.run(main())
