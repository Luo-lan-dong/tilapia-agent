"""诊断：集群负载 / IP / 交付物聚合的数据源现状。"""
import asyncio
import json
import sys

import asyncpg

DSN = "postgresql://opc:xBHXSwscrLPHF3G6@192.168.101.101:5432/opc"


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    try:
        rows = await conn.fetch(
            """
            SELECT name, status, load, reported_ip, reported_api_port, metrics_snapshot
            FROM agent_nodes
            WHERE status IN ('idle', 'busy', 'starting')
            ORDER BY updated_at DESC
            LIMIT 5
            """
        )
        print("== 在线节点 ==")
        for r in rows:
            snap = r["metrics_snapshot"]
            print(
                f"  {r['name']}: status={r['status']} load={r['load']} "
                f"reported_ip={r['reported_ip']} port={r['reported_api_port']}"
            )
            print(f"    metrics_snapshot = {json.dumps(snap, ensure_ascii=False)[:300]}")

        # 交付物聚合的数据源预览（与 get_deliverables 相同口径：recent tasks）
        rows = await conn.fetch(
            """
            SELECT t.id::text, t.title, t.status, t.output_dir, t.updated_at,
                   (SELECT count(*) FROM jsonb_array_elements_text(
                        COALESCE(t.specification->'x', '[]'::jsonb)) x) AS dummy
            FROM tasks t ORDER BY t.updated_at DESC LIMIT 5
            """
        )
        print("== 最近任务（交付物聚合候选） ==")
        for r in rows:
            print(
                f"  {r['id'][:8]} {r['title'][:20]!r} status={r['status']} "
                f"output_dir={r['output_dir']}"
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
