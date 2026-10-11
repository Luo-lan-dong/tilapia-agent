"""检查构建环境：节点服务器上是否有 opc-agent-programming 源码与构建产物目录。"""

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.models.base import AsyncSessionLocal  # noqa: E402
from app.models.bt_mcp_server import BtMcpServer  # noqa: E402
from app.services.bt_mcp_client import BtMcpError, timed_call  # noqa: E402
from node_diag_20260927 import _build  # noqa: E402

SERVER_ID = "3fc19d1d-2a75-4a78-8ef8-28ba1d87822b"

COMMANDS = [
    ("查找构建目录/源码", "ls -d /root/opc-agent-programming /opt/opc-agent-programming /root/build 2>/dev/null; find /root /opt -maxdepth 2 -name 'build-code-agent.sh' 2>/dev/null | head -5"),
    ("查找 tarball 产物", "find /root /opt /tmp -maxdepth 4 -name 'opc-agent-code-*.tar.gz' -newer /etc/hostname 2>/dev/null | head -5; ls -la /root/*.tar.gz 2>/dev/null | head -5"),
    ("当前运行版本", "/opt/opc-agent-code/bin/opc-agent --version 2>/dev/null; cat /opt/opc-agent-code/build-info.json 2>/dev/null"),
]


async def main() -> None:
    async with AsyncSessionLocal() as session:
        server = (
            await session.execute(
                select(BtMcpServer).where(BtMcpServer.id == uuid.UUID(SERVER_ID))
            )
        ).scalars().first()

    for title, command in COMMANDS:
        print(f"\n===== {title} =====")
        client = _build(server)
        try:
            result, _ms = await timed_call(client, "Bash", {"command": command})
            data = result.get("data") or {}
            print(str(data.get("stdout") or result.get("msg") or "")[:2000])
        except BtMcpError as exc:
            print(f"[MCP 错误] {exc}")
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
