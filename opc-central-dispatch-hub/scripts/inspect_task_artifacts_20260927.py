"""排查 todo 不回传：查任务产物树 + tilacode 会话状态。

用法：python scripts/inspect_task_artifacts_20260927.py
"""

import asyncio
import sys
from pathlib import Path

import httpx
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.task import Task  # noqa: E402
from app.models.base import AsyncSessionLocal  # noqa: E402

HUB = "http://127.0.0.1:8000"


async def main() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Task).where(Task.status == "running").limit(3)
        )
        tasks = result.scalars().all()
        if not tasks:
            print("没有 running 任务")
            return
        task = tasks[0]
        task_id = str(task.id)
        print(
            f"任务 {task_id[:8]} status={task.status} progress={task.progress} "
            f"output_dir={task.output_dir}"
        )

    async with httpx.AsyncClient(timeout=30) as client:
        # 产物树（中枢已同步的产物清单）
        resp = await client.get(
            f"{HUB}/api/v1/tasks/{task_id}/artifacts/tree",
            params={"path": ""},
        )
        print(f"\nartifacts tree HTTP {resp.status_code}")
        data = resp.json().get("data") or {}
        items = data.get("items") or []
        print(f"产物条目数: {len(items)}")
        for item in items[:20]:
            print(
                f"  {item.get('type')} {item.get('path')} size={item.get('size')}"
            )


if __name__ == "__main__":
    asyncio.run(main())
