"""查指定会话的压缩点状态与最新会话列表。"""
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
        row = await conn.fetchrow(
            """
            SELECT id::text, title, jsonb_array_length(messages) AS n,
                   context_compaction, updated_at
            FROM task_building_sessions
            WHERE id::text LIKE 'ec5d5a2f%'
            """
        )
        if row:
            print(
                f"ec5d5a2f: {row['n']} 条消息, 更新于 {row['updated_at']}, "
                f"compaction = {row['context_compaction']}"
            )
        else:
            print("ec5d5a2f 不存在")
        rows = await conn.fetch(
            """
            SELECT id::text, jsonb_array_length(messages) AS n, updated_at,
                   context_compaction
            FROM task_building_sessions
            ORDER BY updated_at DESC
            LIMIT 5
            """
        )
        print("最近 5 个会话:")
        for r in rows:
            comp = r["context_compaction"]
            comp_desc = (
                f"摘要 {len((comp or {}).get('summary_text') or '')} 字, 覆盖 {(comp or {}).get('message_count')} 条"
                if comp
                else "NULL"
            )
            print(f"  {r['id'][:8]} {r['n']} 条 | {comp_desc} | {r['updated_at']}")
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
