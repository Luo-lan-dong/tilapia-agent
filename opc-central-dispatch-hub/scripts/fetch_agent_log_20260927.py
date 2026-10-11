"""排查 todo 不回传：拉取执行节点本地日志中 SSE 消费相关记录。

用法：python scripts/fetch_agent_log_20260927.py
"""

import asyncio
import sys
from pathlib import Path

import httpx
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.agent_node import AgentNode  # noqa: E402
from app.models.base import AsyncSessionLocal  # noqa: E402

HUB = "http://127.0.0.1:8000"


async def main() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgentNode).where(AgentNode.status == "busy")
        )
        nodes = result.scalars().all()
        if not nodes:
            print("没有 busy 节点")
            return
        node = nodes[0]
        client_id = node.client_id
        print(f"节点 {node.name} id={str(node.id)[:8]} client_id={client_id}")

    if not client_id:
        print("节点无 client_id，无法拉取日志")
        return

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{HUB}/api/v1/logs/agent-fetch",
            json={"client_id": client_id, "tail_lines": 600, "level": "debug"},
        )
        data = resp.json()
        content = (data.get("data") or {}).get("content", "")
        lines = content.splitlines()
        keywords = ("sse", "todo", "progress", "drain", "subscribe", "consume", "prompt", "session", "error", "warning")
        hits = [
            line
            for line in lines
            if any(k in line.lower() for k in keywords)
        ]
        print(f"日志总行数 {len(lines)}，相关 {len(hits)} 行（末 100 行）：\n")
        for line in hits[-100:]:
            print(line[:260])


if __name__ == "__main__":
    asyncio.run(main())
