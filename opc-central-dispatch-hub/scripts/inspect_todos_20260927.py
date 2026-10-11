"""排查子 Agent 待办（todos）不回传问题：查最近任务的 todos/progress 字段与上报日志。

用法：python scripts/inspect_todos_20260927.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import desc, select  # noqa: E402

from app.models.task import Task  # noqa: E402
from app.models.tool_call_log import ToolCallLog  # noqa: E402
from app.models.base import AsyncSessionLocal  # noqa: E402


async def main() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Task)
            .order_by(desc(Task.created_at))
            .limit(5)
        )
        tasks = result.scalars().all()
        print("=== 最近 5 个任务 ===")
        for task in tasks:
            todos = task.todos or []
            print(
                f"\n[{str(task.id)[:8]}] status={task.status} progress={task.progress} "
                f"stage={task.progress_stage} agent={task.assigned_node_id and str(task.assigned_node_id)[:8]}"
            )
            print(f"  title: {task.title[:50]}")
            print(f"  progress_message: {task.progress_message}")
            print(f"  todos 数量: {len(todos)}")
            for todo in todos[:5]:
                print(
                    f"    - [{todo.get('status')}] {str(todo.get('content'))[:60]}"
                )

        # 查最近 task_progress 相关运行日志（tool_call_logs 不记录 progress，
        # 但查一下最近的 agent 事件辅助判断）
        log_result = await session.execute(
            select(ToolCallLog)
            .where(ToolCallLog.kind == "agent_task_event")
            .order_by(desc(ToolCallLog.created_at))
            .limit(10)
        )
        logs = log_result.scalars().all()
        print("\n=== 最近 agent_task_event 日志（10 条） ===")
        for log in logs:
            print(
                f"[{log.created_at:%m-%d %H:%M:%S}] {log.name} status={log.status} "
                f"task={log.task_id and str(log.task_id)[:8]} args={str(log.args_summary)[:80]}"
            )


if __name__ == "__main__":
    asyncio.run(main())
