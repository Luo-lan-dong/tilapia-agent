"""受控复现实验：创建极简任务并派发到 code 节点，观察事件回传与工作目录。

用法：python scripts/repro_todo_20260927.py
"""

import asyncio
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.agent_node import AgentNode  # noqa: E402
from app.models.base import AsyncSessionLocal  # noqa: E402

HUB = "http://127.0.0.1:8000"

REQUIREMENT = (
    "复现实验：仅在工作目录创建文件 hello.txt，内容为一行 hello，然后结束。"
    "不要创建其他任何文件，不要启动服务。"
)


async def main() -> None:
    # 0. 查 code 节点完整 UUID
    async with AsyncSessionLocal() as session:
        node = (
            await session.execute(
                select(AgentNode).where(AgentNode.name == "code")
            )
        ).scalars().first()
        if node is None:
            print("找不到 code 节点")
            return
        node_id = str(node.id)
        print(f"code 节点 id={node_id} status={node.status}")

    async with httpx.AsyncClient(timeout=60) as client:
        # 1. 创建任务（带最简 specification 避免触发 Planning LLM）
        create_resp = await client.post(
            f"{HUB}/api/v1/tasks",
            json={
                "requirement": REQUIREMENT,
                "specification": {
                    "spec": "# 复现实验\n创建 hello.txt（内容 hello）。",
                    "tasks": "# 任务\n1. [ ] 在工作目录创建 hello.txt，内容为 hello",
                    "checklist": "# 验收\n- [ ] hello.txt 存在且内容为 hello",
                },
            },
        )
        print(f"create HTTP {create_resp.status_code}: {create_resp.text[:300]}")
        task_id = create_resp.json()["data"]["id"]
        print(f"task_id={task_id}")

        # 2. 手动派发到 code 节点
        dispatch_resp = await client.post(
            f"{HUB}/api/v1/tasks/{task_id}/dispatch",
            json={"node_id": node_id},
        )
        print(f"dispatch HTTP {dispatch_resp.status_code}: {dispatch_resp.text[:200]}")

        # 3. 观察 90 秒：轮询任务状态与进度
        for i in range(9):
            await asyncio.sleep(10)
            detail = (await client.get(f"{HUB}/api/v1/tasks/{task_id}")).json()["data"]
            print(
                f"[{i * 10 + 10}s] status={detail['status']} progress={detail['progress']} "
                f"stage={detail.get('progress_stage')} msg={str(detail.get('progress_message'))[:40]} "
                f"todos={len(detail.get('todos') or [])}"
            )
            if detail["status"] in ("completed", "failed", "review"):
                break


if __name__ == "__main__":
    asyncio.run(main())
