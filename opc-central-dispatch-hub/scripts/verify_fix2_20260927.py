"""复测 639abf4e：PATCH 失效是否发出 + tilacode 日志实际报错。"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.models.base import AsyncSessionLocal  # noqa: E402
from app.models.bt_mcp_server import BtMcpServer  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.services.bt_mcp_client import BtMcpError, timed_call  # noqa: E402
from node_diag_20260927 import _build  # noqa: E402

SERVER_ID = "3fc19d1d-2a75-4a78-8ef8-28ba1d87822b"

COMMANDS = [
    (
        "节点日志：639abf4e + PATCH /config 请求",
        "grep -E '639abf4e|PATCH' /opt/opc-agent-code/logs/agent.log | grep -viE 'sk-' | tail -30",
    ),
    (
        "tilacode 日志尾部（全部级别）",
        "tail -40 /root/.local/share/tilacode/log/tilacode.log | grep -viE 'sk-|apiKey' | tail -25",
    ),
    (
        "工作目录是否有产物产生",
        "latest=$(ls -t /opt/opc-agent-code/workspace/ | head -1); echo \"dir=$latest\"; "
        "ls -la \"/opt/opc-agent-code/workspace/$latest/\" 2>&1 | head -14",
    ),
]


async def hub_side() -> None:
    async with AsyncSessionLocal() as session:
        tasks = (
            await session.execute(select(Task).order_by(Task.created_at.desc()).limit(1))
        ).scalars().all()
        for task in tasks:
            todos = task.todos or []
            print(
                f"[{str(task.id)[:8]}] status={task.status} progress={task.progress} "
                f"stage={task.progress_stage} actual={task.executor_model_actual} todos={len(todos)}"
            )
            print(f"  title: {task.title[:56]}")
            print(f"  msg: {task.progress_message}")
            for todo in todos[:6]:
                print(f"    - [{todo.get('status')}] {str(todo.get('content'))[:52]}")


async def node_side(server) -> None:
    for title, command in COMMANDS:
        print(f"\n===== {title} =====")
        client = _build(server)
        try:
            result, _ms = await timed_call(client, "Bash", {"command": command})
            data = result.get("data") or {}
            print(str(data.get("stdout") or result.get("msg") or "")[:2600])
        except BtMcpError as exc:
            print(f"[MCP 错误] {exc}")
        finally:
            await client.close()


async def main() -> None:
    await hub_side()
    async with AsyncSessionLocal() as session:
        server = (
            await session.execute(
                select(BtMcpServer).where(BtMcpServer.id == __import__("uuid").UUID(SERVER_ID))
            )
        ).scalars().first()
    await node_side(server)


if __name__ == "__main__":
    asyncio.run(main())
