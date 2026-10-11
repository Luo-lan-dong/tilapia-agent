"""精查：PATCH 响应状态码 + 后续完整执行链路 + tilacode 日志确认。"""

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
        "节点日志 23:34:13-23:35:00 完整段",
        "grep -n '23:3[45]:' /opt/opc-agent-code/logs/agent.log | grep -viE 'sk-' | head -40",
    ),
    (
        "tilacode 日志 15:3x 段（UTC）",
        "grep -c '2026-09-27T15:3' /root/.local/share/tilacode/log/tilacode.log; "
        "grep '2026-09-27T15:3' /root/.local/share/tilacode/log/tilacode.log | grep -viE 'sk-|apiKey' | head -20",
    ),
    (
        "tilacode 进程与 server 端口监听",
        "ps aux | grep 'tilacode serve' | grep -v grep | head -2; "
        "ss -tlnp 2>/dev/null | grep 4096 | head -3",
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
            print(str(data.get("stdout") or result.get("msg") or "")[:2800])
        except BtMcpError as exc:
            print(f"[MCP 错误] {exc}")
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
