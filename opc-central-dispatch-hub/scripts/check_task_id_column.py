"""检查 task_building_sessions 表的 task_id 引用情况。"""

import asyncio

import asyncpg


async def main() -> None:
    conn = await asyncpg.connect(
        host="192.168.101.101",
        port=5432,
        user="opc",
        password="xBHXSwscrLPHF3G6",
        database="opc",
    )
    try:
        rows = await conn.fetch(
            """
            SELECT tbs.id AS session_id, tbs.task_id, t.title AS task_title
            FROM task_building_sessions tbs
            LEFT JOIN tasks t ON tbs.task_id = t.id
            WHERE tbs.task_id IS NOT NULL
            LIMIT 20
            """
        )
        print("带 task_id 的会话：")
        for row in rows:
            print(f"  session={row['session_id']} task={row['task_id']} title={row['task_title']}")
        print(f"\n共 {len(rows)} 条")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
