"""一次性脚本：将卡住的 running 任务标记为 failed 并释放节点。

仅用于历史遗留数据清理，不属于产品功能。
"""
import asyncio
from datetime import UTC, datetime

from sqlalchemy import text

from app.models.agent_node import NodeStatus
from app.models.base import AsyncSessionLocal
from app.models.task import TaskStatus

STUCK_TASK_IDS: list[str] = [
    "ac74cb06-c259-4b22-a232-922bda5b120d",
    "d1f16732-e483-46ba-a786-c955b88e5330",
]
FAILURE_REASON = "Agent 长时间无进度上报（>24h），手动标记为 failed（卡死兜底功能上线前一次性清理）"


async def main() -> None:
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        for task_id in STUCK_TASK_IDS:
            row = await session.execute(
                text(
                    "SELECT id::text, status, assigned_node_id::text "
                    "FROM tasks WHERE id = :tid"
                ),
                {"tid": task_id},
            )
            task = row.mappings().first()
            if task is None:
                print(f"[skip] {task_id} not found")
                continue
            if task["status"] != TaskStatus.RUNNING.value:
                print(f"[skip] {task_id} status={task['status']} (not running)")
                continue

            node_id = task["assigned_node_id"]
            await session.execute(
                text(
                    "UPDATE tasks SET status = :failed, updated_at = :now, "
                    "change_summary = COALESCE(change_summary, :reason) "
                    "WHERE id = :tid"
                ),
                {
                    "failed": TaskStatus.FAILED.value,
                    "now": now,
                    "reason": FAILURE_REASON,
                    "tid": task_id,
                },
            )
            if node_id is not None:
                await session.execute(
                    text(
                        "UPDATE agent_nodes SET status = :idle, "
                        "load = GREATEST(load - 1, 0) "
                        "WHERE id = :nid"
                    ),
                    {"idle": NodeStatus.IDLE.value, "nid": node_id},
                )
            print(
                f"[done] {task_id} -> failed, node={node_id} released"
            )
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
