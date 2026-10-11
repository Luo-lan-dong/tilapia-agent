"""中枢重启主动反连服务（ADR-035 第二步）。

lifespan 启动完成后异步任务：查询 ``agent_nodes`` 中有自报地址（reported_ip
+ api_token）且状态为 offline 的节点，逐个（并发上限
``REVERSE_NOTIFY_CONCURRENCY``）调用其 notify API，携带中枢当前
``hub_public_url``；失败静默（Agent 自身并行竞速兜底），仅打结构化日志。

端口未变化场景的开销控制：任务为启动后一次性执行，且仅针对仍处于 offline
的节点——中枢同端口重启时 Agent 已在启动延迟窗口内自行竞速重连（转为
online 被过滤），反连零流量；跨进程的「上次通知记录」无需持久化。
"""

import asyncio
import logging
from urllib.parse import urlparse, urlunparse

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.models.agent_node import AgentNode, NodeStatus
from app.models.base import AsyncSessionLocal
from app.models.system_settings import SystemSettings

logger = logging.getLogger(__name__)

# 启动反连前的初始延迟（秒）：留出 Agent 自愈重连窗口 + 首轮心跳检查
# 将失联节点标记 offline，避免对已自行恢复的节点做无用通知
_STARTUP_DELAY_SECONDS = 5.0

# WS 地址路径（与 Agent 端 _derive_ws_url 推导规则一致）
_WS_PATH = "/api/v1/agent-nodes/ws"


def _derive_ws_url(api_url: str) -> str:
    """将中枢 REST URL 转换为 Agent WS 地址（http→ws / https→wss）。"""
    parsed = urlparse(api_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, _WS_PATH, "", "", ""))


async def _load_reverse_notify_targets(session) -> tuple[str | None, list[dict]]:
    """读取中枢公网地址与待反连节点列表。

    Args:
        session: 异步数据库会话。

    Returns:
        (hub_public_url 或 None, 目标节点字典列表
        [{node_id, client_id, ip, api_port, api_token}])。
    """
    settings_row = (await session.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
    hub_public_url = settings_row.hub_public_url if settings_row else None

    result = await session.execute(
        select(AgentNode).where(
            AgentNode.status == NodeStatus.OFFLINE.value,
            AgentNode.reported_ip.is_not(None),
            AgentNode.api_token.is_not(None),
            AgentNode.reported_api_port.is_not(None),
        )
    )
    targets = [
        {
            "node_id": node.id,
            "client_id": node.client_id,
            "ip": node.reported_ip,
            "api_port": node.reported_api_port,
            "api_token": node.api_token,
        }
        for node in result.scalars().all()
    ]
    return hub_public_url, targets


async def _notify_agent(
    client: httpx.AsyncClient,
    target: dict,
    hub_public_url: str,
    hub_ws_url: str,
) -> bool:
    """调用单个 Agent 的通知 API，成功返回 True。

    notify 为尽力而为：任何异常（不可达/超时/401 等）仅告警日志，
    不影响其余节点与其他启动流程（Agent 并行竞速兜底重连）。
    """
    settings = get_settings()
    url = f"http://{target['ip']}:{target['api_port']}/internal/hub-notify"
    try:
        response = await client.post(
            url,
            json={"hub_public_url": hub_public_url, "hub_ws_url": hub_ws_url},
            headers={"Authorization": f"Bearer {target['api_token']}"},
            timeout=settings.reverse_notify_timeout_seconds,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning(
            "reverse_notify_node_failed",
            extra={
                "event": "reverse_notify_node_failed",
                "node_id": str(target["node_id"]),
                "client_id": target["client_id"],
                "error": str(exc),
            },
        )
        return False
    logger.info(
        "reverse_notify_node_notified",
        extra={
            "event": "reverse_notify_node_notified",
            "node_id": str(target["node_id"]),
            "client_id": target["client_id"],
        },
    )
    return True


async def run_reverse_notify() -> None:
    """中枢重启反连主流程：延迟启动 → 读取目标 → 并发反连（一次性）。"""
    await asyncio.sleep(_STARTUP_DELAY_SECONDS)
    try:
        async with AsyncSessionLocal() as session:
            hub_public_url, targets = await _load_reverse_notify_targets(session)

        if not hub_public_url:
            logger.info(
                "reverse_notify_skipped_no_hub_url",
                extra={"event": "reverse_notify_skipped_no_hub_url"},
            )
            return
        if not targets:
            logger.info(
                "reverse_notify_no_targets",
                extra={"event": "reverse_notify_no_targets"},
            )
            return

        hub_ws_url = _derive_ws_url(hub_public_url)
        semaphore = asyncio.Semaphore(get_settings().reverse_notify_concurrency)

        async def _bounded(client: httpx.AsyncClient, target: dict) -> bool:
            """受并发上限约束的单节点反连。"""
            async with semaphore:
                return await _notify_agent(client, target, hub_public_url, hub_ws_url)

        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(*(_bounded(client, target) for target in targets))

        logger.info(
            "reverse_notify_completed",
            extra={
                "event": "reverse_notify_completed",
                "total": len(targets),
                "notified": sum(1 for ok in results if ok),
            },
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "reverse_notify_failed",
            extra={"event": "reverse_notify_failed"},
        )


def start_reverse_notify() -> asyncio.Task[None]:
    """启动中枢重启反连任务（fire-and-forget，异常在任务内消化）。

    Returns:
        后台任务对象，供 lifespan 关闭时取消。
    """
    return asyncio.create_task(run_reverse_notify())
