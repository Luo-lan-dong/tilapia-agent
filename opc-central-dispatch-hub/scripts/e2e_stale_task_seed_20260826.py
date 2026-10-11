"""E2E 验证：向数据库插入 stale 与 fresh running 任务，等待兜底。

按 ADR-015 / spec/task-stale-detector-spec.md §10 验收：

1. stale 任务（updated_at = NOW - 31 min）→ 期望 10s 内变 failed。
2. fresh 任务（updated_at = NOW）→ 期望保持 running。
3. change_summary 已存在的 stale 任务 → 期望 change_summary 不被覆写。
4. 节点 load 期望减 1。

本脚本只负责插入/查询，不做断言（断言在 E2E 验证下一步）。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.models.agent_node import AgentNode, NodeStatus
from app.models.base import AsyncSessionLocal


async def main() -> None:
    marker = uuid.uuid4().hex[:8]
    print(f"[e2e-seed] marker={marker}")

    async with AsyncSessionLocal() as session:
        # 选一个现有编程类节点，并直接占用（不依赖任务派发）
        node_row = await session.execute(
            select(AgentNode)
            .where(AgentNode.agent_type == "programming")
            .order_by(AgentNode.created_at.asc())
            .limit(1)
        )
        node: AgentNode | None = node_row.scalar_one_or_none()
        if node is None:
            print("[e2e-seed] no programming node found")
            return

        print(
            f"[e2e-seed] using node id={node.id} status={node.status} load={node.load}"
        )

        # 占用节点：status=busy, load+=1（模拟有任务在跑）
        await session.execute(
            text(
                "UPDATE agent_nodes SET status = :busy, load = load + 1 "
                "WHERE id = :nid"
            ),
            {"busy": NodeStatus.BUSY.value, "nid": node.id},
        )
        await session.commit()

        now = datetime.now(UTC)

        # 任务 A：stale，31 分钟前更新，无 change_summary
        task_a_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO tasks (id, title, agent_type, status, progress, "
                "source, execution_mode, assigned_node_id, change_summary, "
                "created_at, updated_at) "
                "VALUES (:id, :title, 'programming', 'running', 0, "
                "'e2e-test', 'build', :nid, NULL, :created, :updated)"
            ),
            {
                "id": task_a_id,
                "title": f"e2e-stale-{marker}",
                "nid": node.id,
                "created": now - timedelta(minutes=31),
                "updated": now - timedelta(minutes=31),
            },
        )

        # 任务 B：stale，31 分钟前更新，但 change_summary 已有内容
        summary_b = "本轮修改：fix bug #456（E2E 预置）"
        task_b_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO tasks (id, title, agent_type, status, progress, "
                "source, execution_mode, assigned_node_id, change_summary, "
                "created_at, updated_at) "
                "VALUES (:id, :title, 'programming', 'running', 0, "
                "'e2e-test', 'build', :nid, :cs, :created, :updated)"
            ),
            {
                "id": task_b_id,
                "title": f"e2e-stale-preserved-{marker}",
                "nid": node.id,
                "cs": summary_b,
                "created": now - timedelta(minutes=31),
                "updated": now - timedelta(minutes=31),
            },
        )

        # 任务 C：fresh，1 分钟前更新
        task_c_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO tasks (id, title, agent_type, status, progress, "
                "source, execution_mode, assigned_node_id, change_summary, "
                "created_at, updated_at) "
                "VALUES (:id, :title, 'programming', 'running', 0, "
                "'e2e-test', 'build', :nid, NULL, :created, :updated)"
            ),
            {
                "id": task_c_id,
                "title": f"e2e-fresh-{marker}",
                "nid": node.id,
                "created": now - timedelta(minutes=1),
                "updated": now - timedelta(minutes=1),
            },
        )

        await session.commit()

        print(f"[e2e-seed] task A stale id={task_a_id} (无 change_summary)")
        print(f"[e2e-seed] task B stale id={task_b_id} (change_summary 已存在)")
        print(f"[e2e-seed] task C fresh id={task_c_id} (1 分钟前更新)")


if __name__ == "__main__":
    asyncio.run(main())
