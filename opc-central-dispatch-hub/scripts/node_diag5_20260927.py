"""排查第五轮：tilacode 二进制实际读取的配置目录（opencode vs tilacode）。"""

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
    ("opencode 旧目录是否存在", "ls -la /root/.config/opencode/ 2>&1 | head -8"),
    ("opencode 目录的 jsonc", "cat /root/.config/opencode/opencode.jsonc 2>/dev/null | head -30; cat /root/.config/opencode/tilacode.jsonc 2>/dev/null | head -30"),
    ("tilacode 版本", "/opt/opc-agent-code/bin/tilacode --version 2>&1 | head -3"),
    ("tilacode 进程打开的配置文件", "ls -la /proc/288263/cwd 2>&1; cat /proc/288263/cmdline 2>/dev/null | tr '\\0' ' '; echo"),
    ("tilacode 日志中配置加载记录", "grep -inE 'config|provider' /root/.local/share/tilacode/log/tilacode.log | tail -15"),
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
            print(str(data.get("stdout") or result.get("msg") or "")[:2200])
        except BtMcpError as exc:
            print(f"[MCP 错误] {exc}")
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
