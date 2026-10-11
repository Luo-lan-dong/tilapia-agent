"""复测三：tilacode 新 run 记录 + 节点到 DeepSeek API 连通性。"""

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
        "tilacode 日志 22:14 后的记录",
        "awk '/2026-09-27T14:1[4-9]|2026-09-27T14:[2-9]/' /root/.local/share/tilacode/log/tilacode.log | grep -vE 'sk-|apiKey' | head -30",
    ),
    (
        "节点到 DeepSeek API 连通性",
        "curl -s -m 8 -o /dev/null -w 'models:%{http_code} time:%{time_total}s\\n' https://api.deepseek.com/v1/models -H 'Authorization: Bearer sk-b82c3cdb72a94e089eb8ec10020b0d57' 2>&1",
    ),
    (
        "tilacode 日志全文尾部（不限错误级）",
        "tail -25 /root/.local/share/tilacode/log/tilacode.log | grep -vE 'sk-|apiKey'",
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
