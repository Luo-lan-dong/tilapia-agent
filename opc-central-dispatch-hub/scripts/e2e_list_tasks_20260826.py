"""列出所有 e2e-test 任务现状，决定如何清理 + 重做。"""

import asyncio

from sqlalchemy import text

from app.models.base import AsyncSessionLocal


async def main() -> None:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id::text, title, status, assigned_node_id::text, "
                    "change_summary, updated_at FROM tasks "
                    "WHERE source IN ('e2e-test', 'e2e-test-v2') "
                    "ORDER BY created_at"
                )
            )
        ).mappings().all()
        if not rows:
            print("  (no e2e tasks found)")
        for r in rows:
            print(
                f"  {r['id']}  title={r['title']!r}  status={r['status']}  "
                f"node={r['assigned_node_id']}  cs={(r['change_summary'] or '')[:30]!r}  "
                f"updated_at={r['updated_at']}"
            )


if __name__ == "__main__":
    asyncio.run(main())
