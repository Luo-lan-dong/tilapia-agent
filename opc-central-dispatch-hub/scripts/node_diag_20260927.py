"""排查 todo 不回传：通过宝塔 MCP（节点本机）执行诊断命令查 tilacode 现场。

用法：python scripts/node_diag_20260927.py
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.base import AsyncSessionLocal  # noqa: E402
from app.models.bt_mcp_server import BtMcpServer  # noqa: E402
from app.services.bt_mcp_client import BtMcpError, timed_call  # noqa: E402

SERVER_ID = "3fc19d1d-2a75-4a78-8ef8-28ba1d87822b"

COMMANDS = [
    ("tilacode/agent 进程", "ps aux | grep -E 'tilacode|opc-agent' | grep -v grep | head -8"),
    ("workspace 目录", "ls -la /opt/opc-agent-code/workspace/ 2>&1 | tail -8"),
    ("复现任务工作目录", "ls -la '/opt/opc-agent-code/workspace/复现实验：仅在工作目录创建文件-hell-02d9a17d/' 2>&1"),
    ("该目录 .tilacode 配置", "cat '/opt/opc-agent-code/workspace/复现实验：仅在工作目录创建文件-hell-02d9a17d/.tilacode/tilacode.jsonc' 2>&1 | head -40"),
    ("tilacode server 活性", "curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:4096/ ; echo"),
    ("Agent 日志尾部", "tail -40 /opt/opc-agent-programming/logs/agent.log 2>/dev/null || ls -la /opt/opc-agent-programming/ 2>&1 | head -15"),
]


async def main() -> None:
    async with AsyncSessionLocal() as session:
        server = (
            await session.execute(
                select(BtMcpServer).where(BtMcpServer.id == __import__("uuid").UUID(SERVER_ID))
            )
        ).scalars().first()
    if server is None:
        print("找不到宝塔服务器配置")
        return

    for title, command in COMMANDS:
        print(f"\n===== {title} =====")
        print(f"$ {command}")
        client = _build(server)
        try:
            result, _ms = await timed_call(client, "Bash", {"command": command})
            status = result.get("status")
            data = result.get("data") or {}
            stdout = str(data.get("stdout") or result.get("msg") or "")
            print(stdout[:2000])
            if not status:
                print(f"[工具报错] msg={result.get('msg')}")
        except BtMcpError as exc:
            print(f"[MCP 错误] {exc}")
        finally:
            await client.close()


def _build(server: BtMcpServer):
    from app.skills.bt_mcp import _SERVICE  # noqa: PLC0415

    return _SERVICE.build_client(server)


if __name__ == "__main__":
    asyncio.run(main())
