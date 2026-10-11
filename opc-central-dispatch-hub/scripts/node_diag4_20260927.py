"""排查第四轮：用户级配置文件内容 + Agent/tilacode 进程的配置目录环境变量。"""

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
    ("用户级 tilacode.jsonc（provider 定义应在此）", "cat /root/.config/tilacode/tilacode.jsonc 2>&1 | head -60"),
    ("tilacode server 进程配置相关 env", "cat /proc/288263/environ 2>/dev/null | tr '\\0' '\\n' | grep -iE 'xdg|config|tilacode|home' | head -10"),
    ("Agent 主进程配置相关 env", "cat /proc/288258/environ 2>/dev/null | tr '\\0' '\\n' | grep -iE 'xdg|config|tilacode|app_env' | head -10"),
    ("XDG 其他位置", "ls -la /root/.config/tilacode/ 2>&1; find /root -maxdepth 4 -name 'tilacode.jsonc' 2>/dev/null | head -5"),
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
            print(str(data.get("stdout") or result.get("msg") or "")[:2500])
        except BtMcpError as exc:
            print(f"[MCP 错误] {exc}")
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
