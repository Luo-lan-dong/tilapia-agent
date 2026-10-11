"""排查：查 llm_providers 表的模型配置与使用中状态。

用法：python scripts/inspect_llm_providers_20260927.py
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.base import AsyncSessionLocal  # noqa: E402
from app.models.llm_provider import LLMProvider  # noqa: E402


def mask(value: str | None) -> str:
    if not value:
        return "<empty>"
    return f"<len={len(value)}>"


async def main() -> None:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(select(LLMProvider).order_by(LLMProvider.created_at))
        ).scalars().all()
        print(f"共 {len(rows)} 个模型配置：")
        for row in rows:
            print(
                f"\n[{str(row.id)[:8]}] provider={row.provider} model={row.model} "
                f"is_active={getattr(row, 'is_active', None)} "
                f"supports_vision={getattr(row, 'supports_vision', None)} "
                f"base_url={row.base_url}"
            )
            print(f"  api_key: {mask(row.api_key)}")
            for col in row.__table__.columns:
                if col.name in ("id", "provider", "model", "is_active", "supports_vision", "base_url", "api_key"):
                    continue
                print(f"  {col.name}: {getattr(row, col.name)}")


if __name__ == "__main__":
    asyncio.run(main())
