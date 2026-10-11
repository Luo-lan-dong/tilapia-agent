"""查询 agent_nodes 表的监控数据，验证后端采集链路。"""

import asyncio
import json
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://opc:opc@localhost:5432/opc",
)

engine = create_async_engine(DATABASE_URL, echo=False)


async def main() -> None:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT
                    id,
                    name,
                    agent_type,
                    status,
                    load,
                    client_id,
                    last_heartbeat_at,
                    metrics_snapshot
                FROM agent_nodes
                ORDER BY last_heartbeat_at DESC NULLS LAST
                """
            )
        )
        rows = result.mappings().all()

    print(f"共查询到 {len(rows)} 个节点\n")
    for row in rows:
        metrics = row["metrics_snapshot"]
        has_metrics = isinstance(metrics, dict) and bool(metrics)
        print(f"节点 ID: {row['id']}")
        print(f"  名称: {row['name']}")
        print(f"  类型: {row['agent_type']}")
        print(f"  状态: {row['status']}")
        print(f"  负载(load): {row['load']}")
        print(f"  client_id: {row['client_id']}")
        print(f"  最后心跳: {row['last_heartbeat_at']}")
        print(f"  是否携带监控快照: {has_metrics}")
        if has_metrics:
            print("  metrics_snapshot 摘要:")
            print(json.dumps(metrics, indent=4, ensure_ascii=False))
        else:
            print("  metrics_snapshot: None / 空")
        print("-" * 60)


if __name__ == "__main__":
    asyncio.run(main())
