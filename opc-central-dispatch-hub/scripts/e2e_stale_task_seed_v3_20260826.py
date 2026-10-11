"""E2E v3：基于 idle 活动节点（3f7bb90f）重新做 stale detector 验证。

关键修正：
1. 节点必须 status=idle 且正在心跳（hb 不为空），不会被 node_heartbeat reset 抢跑
2. 节点 load +1 仅作"模拟占用"标记，不改 status（保留 idle）
3. 三条任务 status=running + assigned_node_id=该节点 + updated_at=31 分钟前
4. fresh 任务 updated_at=1 分钟前

stale detector 一轮（约 10s）应自动把 A/B 标 failed 并保留 B 的 change_summary，
释放该节点 load -1；C 保持 running。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text

from app.models.agent_node import AgentNode
from app.models.base import AsyncSessionLocal
from app.models.task import Task


async def main() -> None:
    marker = uuid.uuid4().hex[:8]
    print(f"[e2e-seed-v3] marker={marker}")

    async with AsyncSessionLocal() as session:
        # 1. 清理 v1/v2 脏数据（仅 source IN 'e2e-test*'）
        await session.execute(
            delete(Task).where(Task.source.in_(["e2e-test", "e2e-test-v2"]))
        )
        await session.commit()
        print("[e2e-seed-v3] cleaned v1/v2 e2e tasks")

        # 2. 选 idle 活动节点
        node_row = await session.execute(
            select(AgentNode)
            .where(
                AgentNode.agent_type == "programming",
                AgentNode.status == "idle",
                AgentNode.last_heartbeat_at.is_not(None),
            )
            .order_by(AgentNode.load.asc())
            .limit(1)
        )
        node: AgentNode | None = node_row.scalar_one_or_none()
        if node is None:
            print("[e2e-seed-v3] no idle heartbeat node found")
            return

        print(
            f"[e2e-seed-v3] using node id={node.id} status={node.status} load={node.load} "
            f"hb={node.last_heartbeat_at}"
        )

        # 3. +1 load 模拟"该节点占用了 1 个任务"
        await session.execute(
            text("UPDATE agent_nodes SET load = load + 1 WHERE id = :nid"),
            {"nid": node.id},
        )
        await session.commit()

        # 4. 插入 3 条任务
        now = datetime.now(UTC)
        task_a = uuid.uuid4()
        task_b = uuid.uuid4()
        task_c = uuid.uuid4()
        summary_b = "本轮修改：fix bug #v3（E2E 预置 v3）"

        await session.execute(
            text(
                "INSERT INTO tasks (id, title, agent_type, status, progress, "
                "source, execution_mode, assigned_node_id, change_summary, "
                "created_at, updated_at) VALUES "
                "(:id, :t, 'programming', 'running', 0, 'e2e-v3', 'build', :nid, :cs, :c, :u)"
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

        # 5. 写 marker
        with open("logs/e2e_v3_marker.txt", "w", encoding="utf-8") as f:
            f.write(
                f"node_id={node.id}\n"
                f"task_a={task_a}\n"
                f"task_b={task_b}\n"
                f"task_c={task_c}\n"
                f"summary_b={summary_b}\n"
                f"node_load_before=+1\n"
            )

        print(f"[e2e-seed-v3] task A stale id={task_a} (无 change_summary)")
        print(f"[e2e-seed-v3] task B stale id={task_b} (change_summary 已存在)")
        print(f"[e2e-seed-v3] task C fresh id={task_c} (1 分钟前更新)")
        print("[e2e-seed-v3] marker file -> logs/e2e_v3_marker.txt")


if __name__ == "__main__":
    asyncio.run(main())
