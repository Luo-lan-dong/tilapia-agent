"""排查第二轮：tilacode 数据目录与会话事件存储。"""

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
    ("tilacode 进程环境变量", "cat /proc/288263/environ 2>/dev/null | tr '\\0' '\\n' | grep -viE 'key|token|password|secret' | head -25"),
    ("tilacode 数据目录", "ls -la /root/.local/share/tilacode/ 2>/dev/null; ls -la /root/.local/share/opencode/ 2>/dev/null | head -10"),
    ("日志目录探测", "ls -la /opt/opc-agent-code/logs/ 2>/dev/null | tail -5; journalctl -u tilapia-agent --no-pager -n 5 2>/dev/null | head -8"),
    ("tilacode 全局存储中 02d9a17d 会话", "grep -rl '02d9a17d' /root/.local/share/ 2>/dev/null | head -5"),
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
            print(str(data.get("stdout") or result.get("msg") or "")[:1800])
        except BtMcpError as exc:
            print(f"[MCP 错误] {exc}")
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
