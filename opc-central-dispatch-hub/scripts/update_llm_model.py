"""更新数据库中活跃的 LLM Provider 模型名称。

运行期 LLM 配置从数据库读取，仅修改 .env 不会生效，因此需要同步更新
llm_providers 表中 is_active=true 记录的 model 字段。
"""

import asyncio
import os
import sys

# 将项目根目录加入路径，以便导入 app 模块
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.models.llm_provider import LLMProvider  # noqa: E402


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)

    async with engine.begin() as conn:
        result = await conn.execute(
            select(LLMProvider.id, LLMProvider.model).where(LLMProvider.is_active.is_(True)),
        )
        active_providers = result.all()

        if not active_providers:
            print("未找到活跃的 LLM Provider 记录")
            return

        for provider_id, old_model in active_providers:
            new_model = "deepseek-v4-pro"
            if old_model == new_model:
                print(f"Provider {provider_id} 已经是 {new_model}，跳过")
                continue

            await conn.execute(
                update(LLMProvider)
                .where(LLMProvider.id == provider_id)
                .values(model=new_model),
            )
            print(f"Provider {provider_id}: {old_model} -> {new_model}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
