# -*- coding: utf-8 -*-
"""排查用：查询最近任务的 todos/progress 落库情况与节点版本信息。"""
import asyncio
import json

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
            SELECT id, title, status, progress,
                   CASE WHEN todos IS NULL THEN 'NULL'
                        ELSE jsonb_array_length(todos)::text END AS todo_count,
                   assigned_node_id, created_at
            FROM tasks
            ORDER BY created_at DESC
            LIMIT 10
            """
        )
        print("== 最近 10 条任务 ==")
        for r in rows:
            print(
                f"{str(r['id'])[:8]} | {r['status']:<10} | p={r['progress']:<3} | "
                f"todos={r['todo_count']:<4} | {r['title'][:30]}"
            )

        todo_rows = await conn.fetch(
            """
            SELECT id, title, status, progress, todos
            FROM tasks
            WHERE todos IS NOT NULL AND jsonb_array_length(todos) > 0
            ORDER BY created_at DESC
            LIMIT 5
            """
        )
        print("\n== 有 todos 数据的任务（最多 5 条）==")
        if not todo_rows:
            print("（无 —— 说明从未有 todos 落库）")
        for r in todo_rows:
            print(f"{str(r['id'])[:8]} | {r['status']} | {json.dumps(r['todos'], ensure_ascii=False)[:200]}")

        row = await conn.fetchrow(
            """
            SELECT id, status, progress, created_at, updated_at, assigned_node_id
            FROM tasks WHERE id = 'eadbf295-fdd7-459c-98f6-91b5a9b686ec'
            """
        )
        print("== eadbf295 任务详情 ==")
        print(dict(row) if row else "未找到")

        nodes = await conn.fetch(
            """
            SELECT id, name, agent_type, status, load, last_heartbeat_at
            FROM agent_nodes
            ORDER BY last_heartbeat_at DESC NULLS LAST
            LIMIT 10
            """
        )
        print("\n== 节点列表 ==")
        for n in nodes:
            print(
                f"{str(n['id'])[:8]} | {n['name']:<20} | {n['agent_type']} | "
                f"{n['status']:<8} | load={n['load']} | hb={n['last_heartbeat_at']}"
            )
    finally:
        await conn.close()


asyncio.run(main())
