"""复测二：直接 grep 任务执行痕迹与 tilacode 错误。"""

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
        "节点日志：6030e150 全部痕迹",
        "grep -c '6030e150' /opt/opc-agent-code/logs/agent.log 2>/dev/null; "
        "grep '6030e150' /opt/opc-agent-code/logs/agent.log | grep -viE 'sk-' | tail -30",
    ),
    (
        "tilacode 日志：6030e150 / 错误",
        "grep -iE 'error|unavailable' /root/.local/share/tilacode/log/tilacode.log | tail -8; "
        "grep -c '6030e150' /root/.local/share/tilacode/log/tilacode.log 2>/dev/null",
    ),
    (
        "tilacode 进程与会话活动",
        "ps aux | grep 'tilacode serve' | grep -v grep | head -3; "
        "ls -la /opt/opc-agent-code/workspace/极简易测试用登录页面-+-Flask-登-6030e150/ 2>&1 | head -12",
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
