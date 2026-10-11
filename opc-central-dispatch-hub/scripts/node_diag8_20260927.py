"""解卡 + 探针：精简用户级配置停止重启循环；验证新 tilacode 对项目配置 providers 的支持。"""

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
        "备份并精简用户级配置",
        "cp /root/.config/tilacode/tilacode.jsonc /root/.config/tilacode/tilacode.jsonc.bak-20260927 && "
        "printf '{\\n  \"$schema\": \"https://opencode.ai/config.json\"\\n}\\n' > /root/.config/tilacode/tilacode.jsonc && "
        "cat /root/.config/tilacode/tilacode.jsonc",
    ),
    (
        "探针：项目配置含 providers 能否启动（4097 试验口，8 秒超时）",
        "mkdir -p /tmp/tilacode-probe/.tilacode && "
        "printf '{\\n  \"$schema\": \"https://opencode.ai/config.json\",\\n  \"providers\": { \"probe-p\": { \"npm\": \"@ai-sdk/openai-compatible\", \"name\": \"p\", \"options\": { \"baseURL\": \"https://api.deepseek.com\", \"apiKey\": \"sk-probe\" }, \"models\": { \"probe-m\": { \"name\": \"probe-m\" } } } }\\n}\\n' > /tmp/tilacode-probe/.tilacode/tilacode.jsonc && "
        "cd /tmp/tilacode-probe && timeout 8 /opt/opc-agent-code/bin/tilacode serve --hostname 127.0.0.1 --port 4097 2>&1 | head -6; echo EXIT=$?",
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
            print(str(data.get("stdout") or result.get("msg") or "")[:1500])
        except BtMcpError as exc:
            print(f"[MCP 错误] {exc}")
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
