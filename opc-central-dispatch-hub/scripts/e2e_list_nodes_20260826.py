"""查看当前所有节点状态，用于决定 E2E 路径。"""

import asyncio

from sqlalchemy import text

from app.models.base import AsyncSessionLocal


async def main() -> None:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id::text, name, agent_type, status, load, "
                    "last_heartbeat_at FROM agent_nodes "
                    "ORDER BY status, last_heartbeat_at DESC NULLS LAST"
                )
            )
        ).mappings().all()
        for r in rows:
            print(
                f"  {r['id']}  name={r['name']}  type={r['agent_type']}  "
                f"status={r['status']}  load={r['load']}  hb={r['last_heartbeat_at']}"
            )


if __name__ == "__main__":
    asyncio.run(main())
