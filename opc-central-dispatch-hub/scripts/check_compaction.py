"""查询最近的任务构建会话是否触发过上下文压缩（ADR-041 验证脚本）。

用法：python scripts/check_compaction.py
"""
import asyncio
import json
import sys

import asyncpg

DSN = "postgresql://opc:xBHXSwscrLPHF3G6@192.168.101.101:5432/opc"

SQL = """
SELECT id, title, model, updated_at,
       context_compaction AS compaction_raw,
       context_compaction->>'message_count' AS compaction_message_count,
       context_compaction->>'created_at' AS compaction_created_at,
       length(context_compaction->>'summary_text') AS summary_len,
       jsonb_array_length(messages) AS message_count
FROM task_building_sessions
ORDER BY updated_at DESC
LIMIT 15
"""


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    try:
        rows = await conn.fetch(SQL)
        if not rows:
            print("没有会话记录")
            return
        for r in rows:
            raw = r["compaction_raw"]
            has_summary = bool(raw and raw.get("summary_text"))
            flag = "已压缩" if has_summary else "未压缩"
            print(
                f"[{flag}] {str(r['id'])[:8]}… {r['title'][:24]!r} "
                f"model={r['model']} 消息数={r['message_count']} "
                f"更新于 {r['updated_at']}"
            )
            if has_summary:
                print(
                    f"    └ 压缩点: 覆盖前 {r['compaction_message_count']} 条, "
                    f"摘要 {r['summary_len']} 字, 于 {r['compaction_created_at']}"
                )
            elif raw is not None:
                print(f"    └ 异常: context_compaction 非空但无摘要, 原始值={raw}")
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
