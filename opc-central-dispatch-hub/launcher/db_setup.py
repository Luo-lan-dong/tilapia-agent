"""数据库迁移与初始数据（ADR-021，幂等）。

- 迁移：进程内调用 Alembic Python API 执行 ``upgrade head``（避免子进程依赖）。
- 初始数据：确保 ``system_versions`` 存在当前版本记录（对齐 ADR-018 脚本逻辑）。

关键约束：本模块**禁止在模块顶层 import app 模块**。launcher 在注入环境变量
（DATABASE_URL 等）之前加载本模块；app.models.base 顶层会创建 engine 并
触发 get_settings() 的 lru_cache 缓存，若在注入前实例化，将缓存
.env.local 的数据库 URL，导致迁移/应用连错库（WinError 121 超时的根因）。
所有 app 模块 import 必须延迟到函数内部，且仅在环境注入之后被调用。

依赖：调用前必须已注入环境变量，且 CWD 为应用根目录。
"""

import asyncio
import logging

from launcher.errors import MigrationError

logger = logging.getLogger(__name__)

INITIAL_VERSION = "v0.5.0"  # ADR-026：中枢当前版本基线


def run_migrations(app_root: str) -> None:
    """执行 Alembic 迁移至最新版本（幂等）。

    Args:
        app_root: 应用根目录（含 alembic.ini 与 alembic/ 脚本目录）。
    """
    try:
        # 延迟 import：env.py 会调用 get_settings()，必须在环境注入之后
        from alembic.config import Config

        from alembic import command

        cfg = Config(f"{app_root}/alembic.ini")
        cfg.set_main_option("script_location", f"{app_root}/alembic")
        command.upgrade(cfg, "head")
    except Exception as exc:
        raise MigrationError(f"数据库迁移失败：{exc}") from exc
    logger.info("desktop_migrate_completed")


async def seed_initial_data() -> None:
    """插入初始版本记录（幂等，已存在则跳过）。

    ADR-026 起基线为 v0.5.0；历史更新日志完全云端化（云端资源仓库 hub_releases），
    本地仅承载当前运行版本号。

    结束时 dispose engine：asyncio.run 创建的事件循环随即关闭，
    必须清空连接池，避免 uvicorn 线程的新事件循环复用已关闭循环上的连接
    （表现为 lifespan 阶段 "Event loop is closed" 崩溃）。
    """
    # 延迟 import：models.base 顶层创建 engine，必须在环境注入之后
    from sqlalchemy import select

    from app.models.base import AsyncSessionLocal, engine
    from app.models.system_version import SystemVersion

    try:
        async with AsyncSessionLocal() as session:
            stmt = select(SystemVersion).where(SystemVersion.version == INITIAL_VERSION)
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is not None:
                logger.info("desktop_seed_skipped", extra={"version": INITIAL_VERSION})
                return
            import datetime

            session.add(
                SystemVersion(
                    version=INITIAL_VERSION,
                    title="版本发布管理与中枢更新基线",
                    content=(
                        f"中枢当前运行版本 {INITIAL_VERSION}。\n"
                        "历史更新日志请见「系统设置 → 更新日志」页（云端下发）。"
                    ),
                    release_date=datetime.date(2026, 9, 9),
                    is_current=True,
                )
            )
            await session.commit()
        logger.info("desktop_seed_completed", extra={"version": INITIAL_VERSION})
    finally:
        await engine.dispose()


def setup_database(app_root: str) -> None:
    """迁移 + 初始数据的一站式入口。"""
    run_migrations(app_root)
    asyncio.run(seed_initial_data())
