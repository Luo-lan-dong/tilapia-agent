"""排查：查目标任务 70b82d07 的终态与节点状态。

用法：python scripts/inspect_task_final_20260927.py
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.task import Task  # noqa: E402
from app.models.agent_node import AgentNode  # noqa: E402
from app.models.base import AsyncSessionLocal  # noqa: E402


async def main() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Task).order_by(Task.created_at.desc()).limit(3)
        )
        tasks = result.scalars().all()
        for task in tasks:
            print(
                f"[{str(task.id)[:8]}] status={task.status} progress={task.progress} "
                f"stage={task.progress_stage} todos={len(task.todos or [])} "
                f"node={task.assigned_node_id and str(task.assigned_node_id)[:8]}"
            )
            print(f"  title: {task.title[:60]}")
            print(f"  change_summary: {str(task.change_summary)[:200]}")
            print(f"  executor_model: {task.executor_model}")
            print(f"  executor_model_actual: {task.executor_model_actual}")
            print(f"  output_dir: {task.output_dir}")
            print()

        nodes = (await session.execute(select(AgentNode))).scalars().all()
        for node in nodes:
            if node.status in ("busy", "idle"):
                print(
                    f"node {node.name} status={node.status} load={node.load} "
                    f"ip={node.reported_ip} client_id={node.client_id}"
                )


if __name__ == "__main__":
    asyncio.run(main())
