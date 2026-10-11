"""Alembic 迁移环境配置（异步）。"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings
from app.models.agent_node import AgentNode  # noqa: F401 注册 agent_nodes 表
from app.models.base import Base
from app.models.llm_provider import LLMProvider  # noqa: F401 注册 llm_providers 表
from app.models.system_settings import SystemSettings  # noqa: F401 注册 system_settings 表
from app.models.task import Task  # noqa: F401 注册 tasks 表
from app.models.task_building_session import TaskBuildingSession  # noqa: F401

settings = get_settings()
config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False：保留既有 logger（launcher 日志），
    # 否则 fileConfig 会禁用迁移之后的所有 launcher 日志记录
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# 从 Pydantic Settings 读取数据库 URL，覆盖 alembic.ini 中的占位值
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式运行迁移（生成 SQL 脚本）。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """在线模式下在连接上执行迁移。"""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """在线异步模式运行迁移。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
