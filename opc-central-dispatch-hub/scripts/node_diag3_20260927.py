"""排查第三轮：Agent 日志 + tilacode 数据库会话事件。"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.models.base import AsyncSessionLocal  # noqa: E402
from app.models.bt_mcp_server import BtMcpServer  # noqa: E402
from app.services.bt_mcp_client import BtMcpError, timed_call  # noqa: E402
from node_diag_20260927 import _build  # noqa: E402

SERVER_ID = "3fc19d1d-2a75-4a78-8ef8-28ba1d87822b"

COMMANDS = [
    (
        "Agent 日志：02d9a17d 相关",
        "grep -n '02d9a17d' /opt/opc-agent-code/logs/agent.log | tail -25",
    ),
    (
        "Agent 日志：最近 sse/todo/bridge",
        "grep -inE 'sse|todo|drain|bridge_error|prompt' /opt/opc-agent-code/logs/agent.log | tail -25",
    ),
    (
        "tilacode 库表结构",
        "sqlite3 /root/.local/share/tilacode/tilacode-master.db '.tables' 2>&1",
    ),
    (
        "tilacode 日志：02d9a17d",
        "grep -n '02d9a17d' /root/.local/share/tilacode/log/tilacode.log | tail -20",
    ),
]


async def main() -> None:
    async with AsyncSessionLocal() as session:
        server = (
            await session.execute(
                select(BtMcpServer).where(BtMcpServer.id == __import__("uuid").UUID(SERVER_ID))
            )
        ).scalars().first()

    for title, command in COMMANDS:
        print(f"\n===== {title} =====")
        client = _build(server)
        try:
            result, _ms = await timed_call(client, "Bash", {"command": command})
            data = result.get("data") or {}
            print(str(data.get("stdout") or result.get("msg") or "")[:3000])
        except BtMcpError as exc:
            print(f"[MCP 错误] {exc}")
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
