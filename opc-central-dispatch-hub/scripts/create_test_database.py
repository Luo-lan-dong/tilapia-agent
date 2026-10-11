"""一次性脚本：创建独立测试数据库 opc_test（测试库隔离，修复测试清空开发库问题）。

背景：此前 conftest 每个测试都清空 llm_providers/tasks 等表且直接跑在开发库
（opc）上，多次全量测试后用户配置的模型被清掉。修复后测试全部跑在 opc_test。
本脚本幂等：库已存在则跳过。
"""

import asyncio
import os
from urllib.parse import urlparse

import asyncpg


def _parse(url: str) -> dict:
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "",
        "password": parsed.password or "",
    }


async def main() -> None:
    # 开发库 URL 从 .env.local 读取
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env.local")
    dev_url = ""
    with open(env_file, encoding="utf-8") as file:
        for line in file:
            if line.strip().startswith("DATABASE_URL="):
                dev_url = line.split("=", 1)[1].strip()
                break
    if not dev_url:
        raise SystemExit("未在 .env.local 找到 DATABASE_URL")

    params = _parse(dev_url)
    dev_db = urlparse(dev_url).path.lstrip("/")
    test_db = f"{dev_db}_test"

    # pg_hba 通常只授权业务库：直接连开发库执行 CREATE DATABASE（需 CREATEDB 权限）
    conn = await asyncpg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database=dev_db,
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", test_db
        )
        if exists:
            print(f"测试库已存在：{test_db}")
        else:
            await conn.execute(f'CREATE DATABASE "{test_db}"')
            print(f"测试库已创建：{test_db}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
