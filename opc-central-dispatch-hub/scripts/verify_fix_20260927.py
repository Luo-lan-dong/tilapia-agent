"""验证修复部署：任务 ffb1f6f9 全链路检查。"""

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
TASK_ID = "ffb1f6f9"

COMMANDS = [
    (
        "节点日志：ffb1f6f9 执行痕迹",
        "grep 'ffb1f6f9' /opt/opc-agent-code/logs/agent.log | grep -viE 'sk-' | tail -25",
    ),
    (
        "tilacode 日志：最新错误",
        "tail -30 /root/.local/share/tilacode/log/tilacode.log | grep -viE 'sk-|apiKey' | tail -20",
    ),
    (
        "工作目录配置键名（应为单数 provider）",
        "latest=$(ls -t /opt/opc-agent-code/workspace/ | head -1); echo \"dir=$latest\"; "
        "grep -E '\"provider\"|\"providers\"|hub-deepseek' \"/opt/opc-agent-code/workspace/$latest/.tilacode/tilacode.jsonc\" 2>&1",
    ),
]


async def hub_side() -> None:
    async with AsyncSessionLocal() as session:
        task = (
            await session.execute(
                select(Task).where(Task.id == __import__("uuid").UUID("ffb1f6f9-a58f-42ca-90f9-b6742fcfa2c8"))
            )
        ).scalars().first()
        if task is None:
            print("!! 任务不存在")
            return
        todos = task.todos or []
        print(
            f"[{str(task.id)[:8]}] status={task.status} progress={task.progress} "
            f"stage={task.progress_stage} actual={task.executor_model_actual} todos={len(todos)}"
        )
        print(f"  msg: {task.progress_message}")
        for todo in todos[:8]:
            print(f"    - [{todo.get('status')}] {str(todo.get('content'))[:56]}")


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
