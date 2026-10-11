"""Upload 工具协议探测（中枢本机文件直传宝塔服务器的可行性验证）。

流程：Upload(file_name) → 打印首次返回（next_action/curl 命令结构）→
若可推进：中枢本机执行 curl（替换 <LOCAL_FILE> 为真实本地文件）→
Upload(file_id) 轮询 ready → 输出服务器绝对路径。
"""

import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.models.bt_mcp_server import BtMcpServer  # noqa: E402
from app.services.bt_mcp_client import BtMcpClient, timed_call  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import get_settings  # noqa: E402

TEST_LOCAL = Path("data/e2e_upload_probe.txt")


def make_session_factory():
    engine = create_async_engine(get_settings().database_url, echo=False)
    return engine, sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def load_server(factory) -> BtMcpServer:
    async with factory() as session:
        result = await session.execute(
            select(BtMcpServer).where(BtMcpServer.enabled.is_(True))
        )
        server = result.scalars().first()
        session.expunge(server)
        return server


def print_result(tag: str, result) -> None:
    import json

    print(f"--- {tag} ---")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])


async def main() -> int:
    TEST_LOCAL.parent.mkdir(parents=True, exist_ok=True)
    TEST_LOCAL.write_text("opc upload probe content", encoding="utf-8")

    engine, factory = make_session_factory()
    try:
        server = await load_server(factory)
        client = BtMcpClient(
            url=server.local_url if server.address_mode == "local" else (server.public_url or server.local_url),
            api_token=server.api_token,
            ca_cert=server.ca_cert,
        )
        try:
            # 第一步：file_name 换取 next_action
            result, _ = await timed_call(
                client, "Upload", {"file_name": TEST_LOCAL.name, "size": TEST_LOCAL.stat().st_size}
            )
            print_result("首次调用 Upload(file_name)", result)
            if not isinstance(result, dict):
                return 1
            data = result.get("data") or result
            text = str(data)
            if "<LOCAL_FILE>" not in text:
                print("返回中无 <LOCAL_FILE> 占位，协议与预期不同，停止")
                return 1
            # 提取平台命令（next_action.posix / next_action.windows）
            next_action = data.get("next_action") or {}
            command = ""
            if isinstance(next_action, dict):
                command = next_action.get("windows") or next_action.get("posix") or ""
            if not command or "<LOCAL_FILE>" not in command:
                print(f"next_action 中无可执行命令：{str(next_action)[:200]}")
                return 1
            command = command.replace("<LOCAL_FILE>", str(TEST_LOCAL.resolve()))
            print(f"中枢本机执行：{command[:160]}...")
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            print(f"curl 退出码：{proc.returncode}，输出：{stdout.decode(errors='replace')[:500]}")
            if proc.returncode != 0:
                return 1

            # 第二步：file_id 轮询 ready
            file_id = data.get("file_id") or ""
            result2, _ = await timed_call(client, "Upload", {"file_id": file_id})
            print_result("轮询 Upload(file_id)", result2)
            return 0
        finally:
            await client.close()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
