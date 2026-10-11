"""排查：触发任务产物 zip 实时打包，判定节点工作目录是否有文件产生。

用法：python scripts/probe_artifacts_zip_20260927.py
"""

import asyncio
import io
import sys
import zipfile
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
            select(Task).where(Task.status == "running").limit(1)
        )
        task = result.scalars().first()
        if task is None:
            result = await session.execute(
                select(Task).order_by(Task.created_at.desc()).limit(1)
            )
            task = result.scalars().first()
        if task is None:
            print("没有任务")
            return
        task_id = str(task.id)
        print(f"任务 {task_id[:8]} status={task.status} output_dir={task.output_dir}")

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(f"{HUB}/api/v1/tasks/{task_id}/artifacts/download")
        print(f"download HTTP {resp.status_code} content-type={resp.headers.get('content-type')}")
        if resp.status_code != 200 or "zip" not in (resp.headers.get("content-type") or ""):
            print(f"body: {resp.text[:400]}")
            return
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        print(f"zip 条目数: {len(names)}")
        for name in names[:30]:
            info = zf.getinfo(name)
            print(f"  {name} ({info.file_size}B)")


if __name__ == "__main__":
    asyncio.run(main())
