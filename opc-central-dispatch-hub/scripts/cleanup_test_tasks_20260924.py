"""一次性清理脚本（2026-09-24）：删除测试残留的 pending 任务。

背景：全量回归被强制中断时，test_task_lifecycle 的 finally 清理未执行，
开发库残留 pending 的 test-task-* 任务；后续生命周期测试的调度器扫描会
把残留任务一并派发，WS 首条消息断言失败（task_id 不匹配）。
仅删除 title 以 test-task- 开头的 pending 任务，不动真实业务数据。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from app.models.base import AsyncSessionLocal
from app.models.dispatch_decision import DispatchDecision
from app.models.task import Task


async def main() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Task).where(Task.title.like("test-task-%"))
        )
        rows = list(result.scalars().all())
        for row in rows:
            print(f"待删除: id={row.id} title={row.title} status={row.status}")
        if not rows:
            print("无残留任务")
            return
        task_ids = [row.id for row in rows]
        await session.execute(
            delete(DispatchDecision).where(DispatchDecision.task_id.in_(task_ids))
        )
        await session.execute(delete(Task).where(Task.id.in_(task_ids)))
        await session.commit()
        print(f"已删除 {len(rows)} 条残留测试任务（含关联派发审计）")


if __name__ == "__main__":
    asyncio.run(main())
