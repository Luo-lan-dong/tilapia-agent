"""E2E 第二轮：基于 ACTIVE 节点重新验证 stale detector。

修正点：选择 status=online 且正在心跳的节点，避免被 node_heartbeat reset 逻辑抢跑。

按 spec/task-stale-detector-spec.md §10：
1. Task A（stale，无 change_summary）→ status=failed，change_summary 含「自动标记为 failed」
2. Task B（stale，change_summary 已存在）→ status=failed，change_summary 保持原文
3. Task C（fresh，1 分钟前）→ status=running
4. 节点 load-1 释放
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.models.agent_node import AgentNode
from app.models.base import AsyncSessionLocal


async def main() -> None:
    marker = uuid.uuid4().hex[:8]
    print(f"[e2e-seed-v2] marker={marker}")

    async with AsyncSessionLocal() as session:
        node_row = await session.execute(
            select(AgentNode)
            .where(AgentNode.agent_type == "programming", AgentNode.status == "online")
            .order_by(AgentNode.load.asc(), AgentNode.last_heartbeat_at.desc())
            .limit(1)
        )
        node: AgentNode | None = node_row.scalar_one_or_none()
        if node is None:
            print("[e2e-seed-v2] no active online node found, fallback to any idle")
            node_row = await session.execute(
                select(AgentNode)
                .where(AgentNode.agent_type == "programming")
                .order_by(AgentNode.load.asc())
                .limit(1)
            )
            node = node_row.scalar_one_or_none()
        if node is None:
            print("[e2e-seed-v2] no node at all, abort")
            return

        print(
            f"[e2e-seed-v2] using node id={node.id} status={node.status} load={node.load}"
        )

        # 仅 +1 load，不改 status（保留其 online/busy 真实状态）
        await session.execute(
            text("UPDATE agent_nodes SET load = load + 1 WHERE id = :nid"),
            {"nid": node.id},
        )
        await session.commit()

        now = datetime.now(UTC)
        task_a = uuid.uuid4()
        task_b = uuid.uuid4()
        task_c = uuid.uuid4()
        summary_b = "本轮修改：fix bug #789（E2E 预置 v2）"

        # 三条任务都指向 active 节点；B 含 change_summary；C 时间新鲜
        await session.execute(
            text(
                "INSERT INTO tasks (id, title, agent_type, status, progress, "
                "source, execution_mode, assigned_node_id, change_summary, "
                "created_at, updated_at) VALUES "
                "(:id, :t, 'programming', 'running', 0, 'e2e-test-v2', 'build', :nid, :cs, :c, :u)"
            ),
            [
                {
                    "id": task_a, "t": f"e2e-stale-{marker}", "nid": node.id,
                    "cs": None,
                    "c": now - timedelta(minutes=31), "u": now - timedelta(minutes=31),
                },
                {
                    "id": task_b, "t": f"e2e-stale-preserved-{marker}", "nid": node.id,
                    "cs": summary_b,
                    "c": now - timedelta(minutes=31), "u": now - timedelta(minutes=31),
                },
                {
                    "id": task_c, "t": f"e2e-fresh-{marker}", "nid": node.id,
                    "cs": None,
                    "c": now - timedelta(minutes=1), "u": now - timedelta(minutes=1),
                },
            ],
        )
        await session.commit()

        # 记录现场，便于 verify 脚本读取
        with open("logs/e2e_v2_marker.txt", "w", encoding="utf-8") as f:
            f.write(
                f"node_id={node.id}\n"
                f"task_a={task_a}\n"
                f"task_b={task_b}\n"
                f"task_c={task_c}\n"
                f"summary_b={summary_b}\n"
                f"node_load_before=+1\n"
            )

        print(f"[e2e-seed-v2] task A stale id={task_a} (无 change_summary)")
        print(f"[e2e-seed-v2] task B stale id={task_b} (change_summary 已存在)")
        print(f"[e2e-seed-v2] task C fresh id={task_c} (1 分钟前更新)")
        print("[e2e-seed-v2] marker file -> logs/e2e_v2_marker.txt")


if __name__ == "__main__":
    asyncio.run(main())
