"""Agent 资源云端检查定时循环（ADR-022 / ADR-025）。

独立于任务调度器：按 ``RESOURCE_SYNC_INTERVAL_SECONDS`` 周期执行一次
资源检查（仅比对版本，不自动下载）。间隔为 0 时禁用定时检查。
"""

import asyncio
import logging
from pathlib import Path

from app.core.config import get_settings
from app.models.base import AsyncSessionLocal
from app.services.resource_sync_service import ResourceSyncService
from app.services.system_settings_service import SystemSettingsService

logger = logging.getLogger(__name__)


async def run_resource_check_loop(stop_event: asyncio.Event) -> None:
    """按配置间隔执行资源检查，直到 stop_event 置位。"""
    settings = get_settings()
    interval = settings.resource_sync_interval_seconds
    if interval <= 0:
        logger.info(
            "resource_check_loop_disabled",
            extra={"event": "resource_check_loop_disabled"},
        )
        return

    logger.info(
        "resource_check_loop_started",
        extra={"event": "resource_check_loop_started", "interval_seconds": interval},
    )
    while not stop_event.is_set():
        try:
            service = ResourceSyncService(cache_dir=Path(settings.resource_cache_dir))
            async with AsyncSessionLocal() as session:
                settings_service = SystemSettingsService()
                # ADR-025：地址优先从服务端配置接口拉取（失败回退缓存/本地配置）
                resolved = await service.resolve_manifest_url()
                if resolved:
                    await settings_service.set_agent_resource_manifest_url(session, resolved)
                    url = resolved
                else:
                    url = await settings_service.get_agent_resource_manifest_url(session)
            result = await service.check(url)
            logger.info(
                "resource_check_loop_executed",
                extra={"event": "resource_check_loop_executed", "status": result.status},
            )
        except Exception:
            logger.exception(
                "resource_check_loop_failed",
                extra={"event": "resource_check_loop_failed"},
            )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue

    logger.info("resource_check_loop_stopped", extra={"event": "resource_check_loop_stopped"})


def start_resource_check_loop(
    stop_event: asyncio.Event,
) -> asyncio.Task[None] | None:
    """启动资源检查后台任务；间隔为 0 时返回 None。"""
    if get_settings().resource_sync_interval_seconds <= 0:
        return None
    return asyncio.create_task(run_resource_check_loop(stop_event))
