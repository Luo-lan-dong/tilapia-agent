"""排查：读 bt_mcp_servers 拿 192.168.101.13 的连接信息，SSH 查 Agent/tilacode 实况。

用法：python scripts/ssh_inspect_node_20260927.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.models.base import AsyncSessionLocal  # noqa: E402
from app.models.bt_mcp_server import BtMcpServer  # noqa: E402


async def main() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BtMcpServer))
        servers = result.scalars().all()
        for s in servers:
            print(
                f"id={str(s.id)[:8]} name={getattr(s, 'name', '')} "
                f"local={getattr(s, 'local_url', None) or getattr(s, 'local_address', None)} "
                f"public={getattr(s, 'public_url', None) or getattr(s, 'public_address', None)}"
            )
            # 打印全部列名与非敏感值（密码只打印长度）
            for col in s.__table__.columns:
                val = getattr(s, col.name)
                if any(k in col.name.lower() for k in ("key", "password", "secret", "token")):
                    print(f"    {col.name}: <len={len(str(val)) if val else 0}>")
                else:
                    print(f"    {col.name}: {val}")


if __name__ == "__main__":
    asyncio.run(main())
