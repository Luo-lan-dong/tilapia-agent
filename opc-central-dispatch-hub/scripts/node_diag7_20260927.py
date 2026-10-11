"""排查节点重启循环：新 tilacode 版本、包信息、当前配置全貌。"""

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
    ("新 tilacode 版本", "/opt/opc-agent-code/bin/tilacode --version 2>&1 | head -2"),
    ("bin 目录与新包", "ls -la /opt/opc-agent-code/bin/ 2>&1 | head -8; cat /opt/opc-agent-code/build-info.json 2>&1"),
    ("用户级配置全貌", "cat /root/.config/tilacode/tilacode.jsonc 2>&1"),
    ("systemd 单元（谁在拉起 Agent）", "systemctl list-units --type=service 2>/dev/null | grep -iE 'agent|opc|tilapia' | head -5"),
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
