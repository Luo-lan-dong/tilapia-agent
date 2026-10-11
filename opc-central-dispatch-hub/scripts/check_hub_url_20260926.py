"""临时检查：system_settings 中 hub 地址配置。"""

import asyncio
import sys

sys.path.insert(0, ".")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings


async def main() -> None:
    engine = create_async_engine(get_settings().database_url, echo=False)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(text("SELECT hub_public_url, frontend_url FROM system_settings"))
        ).fetchall()
        for row in rows:
            print(row)
        print("env fallback hub_public_url:", get_settings().hub_public_url)
    await engine.dispose()


asyncio.run(main())
