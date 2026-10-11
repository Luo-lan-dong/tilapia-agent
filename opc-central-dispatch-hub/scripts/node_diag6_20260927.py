"""排查收尾：查节点 Agent 安装目录结构（判断热修可行性）。"""

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
    ("Agent 安装目录结构", "ls -la /opt/opc-agent-code/ 2>&1 | head -20"),
    ("是否有 app 源码目录", "ls /opt/opc-agent-code/app/ 2>&1 | head -10; ls /opt/opc-agent-code/app/executor/ 2>&1 | head -8"),
    ("workspace.py 是否存在", "ls -la /opt/opc-agent-code/app/executor/workspace.py 2>&1; head -8 /opt/opc-agent-code/app/executor/workspace.py 2>/dev/null"),
    ("systemd 单元", "systemctl cat tilapia-agent 2>/dev/null | head -20; systemctl status tilacode-server --no-pager 2>&1 | head -5"),
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
