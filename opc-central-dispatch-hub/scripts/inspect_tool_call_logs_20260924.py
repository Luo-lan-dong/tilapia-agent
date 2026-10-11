"""一次性排查/清理脚本（2026-09-24）：tool_call_logs 残留行。

用法：python scripts/inspect_tool_call_logs_20260924.py [--purge]
不带参数仅列出；--purge 清空全部行（运维日志表，可安全清空）。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from app.models.base import AsyncSessionLocal
from app.models.tool_call_log import ToolCallLog


async def main() -> None:
    purge = "--purge" in sys.argv
    async with AsyncSessionLocal() as session:
        if purge:
            result = await session.execute(delete(ToolCallLog))
            await session.commit()
            print(f"已清空 {result.rowcount} 行")
            return
        rows = (
            await session.execute(select(ToolCallLog).order_by(ToolCallLog.created_at))
        ).scalars().all()
        for row in rows:
            print(
                f"id={row.id} kind={row.kind} name={row.name} status={row.status} "
                f"session_id={row.session_id} task_id={row.task_id} created_at={row.created_at}"
            )


if __name__ == "__main__":
    asyncio.run(main())
