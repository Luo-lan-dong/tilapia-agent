"""Linux 办公 Agent 入口（ADR-062）。

流程（断线不退进程，应用内自动重连，见 ADR-020）：
1. 加载配置。
2. 启动连接会话循环：向中枢注册并建立 WebSocket（中枢离线时按
   reconnect_interval_seconds 间隔重试，不退出进程），心跳上报 starting 状态。
3. 装配办公任务桥接层（工具循环，无外部进程依赖，就绪即置 bridge_ready）。
4. 捕获 Ctrl+C / SIGTERM，按 后台任务 → 桥接 → 客户端 顺序优雅关闭。
"""

import asyncio
import logging
import signal
import sys
from collections.abc import Callable
from types import FrameType
from typing import NoReturn

from app.client.hub_client import HubClient
from app.client.notify_server import NotifyServer
from app.config import APP_VERSION, AgentConfig
from app.executor.office_bridge import BridgeCallbacks, OfficeBridge
from app.utils.logger import setup_logging


async def _run_agent(config: AgentConfig) -> None:
    """运行 Agent 主循环，直到收到停止信号（断线不退进程，应用内自动重连）。"""
    client = HubClient(config)
    bridge = OfficeBridge(
        config,
        callbacks=BridgeCallbacks(
            on_progress=client.report_progress,
            on_task_started=client.report_task_started,
            on_completed=client.report_completed,
            on_failed=client.report_failed,
            on_file_tree=client.report_file_tree,
            on_file_chunk=client.send_file_chunk,
            on_file_error=client.report_file_error,
        ),
    )
    client.task_bridge = bridge
    background_tasks: list[asyncio.Task[None]] = []
    # 通知 API 服务（ADR-035）：配置了 API_TOKEN 才启动；失败仅告警降级
    notify_server: NotifyServer | None = None
    if config.api_token:
        notify_server = NotifyServer(
            api_token=config.api_token,
            host=config.agent_api_host,
            port=config.agent_api_port,
            on_notify=client.apply_hub_notify,
            loop=asyncio.get_running_loop(),
        )
        try:
            notify_server.start()
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "notify_server_start_failed_degrade",
                extra={"event": "notify_server_start_failed_degrade", "error": str(exc)},
            )
            notify_server = None

    async def on_session_ready() -> None:
        """连接就绪回调：办公执行器无外部进程依赖，直接恢复状态上报。"""
        await client.restore_session_state()

    try:
        background_tasks.append(asyncio.create_task(client.run_forever(on_session_ready)))
        await asyncio.gather(*background_tasks)
    except asyncio.CancelledError:
        logging.info("agent_run_cancelled")
        raise
    finally:
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        if notify_server is not None:
            notify_server.stop()
        await bridge.shutdown()
        await client.close()


def _create_shutdown_handler(
    main_task: asyncio.Task[None],
) -> Callable[[int, FrameType | None], None]:
    """创建信号处理器，取消主任务以触发优雅关闭。"""

    def _handler(signum: int, _frame: FrameType | None) -> None:
        logging.info("shutdown_signal_received", extra={"signal": signum})
        main_task.cancel()

    return _handler


def main() -> NoReturn:
    """程序入口。"""
    # --version 快速路径：安装脚本用它做二进制冒烟校验（不加载 .env 不建连）
    if "--version" in sys.argv:
        print(f"opc-agent {APP_VERSION}")
        sys.exit(0)

    config = AgentConfig()
    setup_logging(config.app_env, config.log_level)
    logger = logging.getLogger(__name__)

    logger.info(
        "agent_starting",
        extra={
            "agent_name": config.resolved_name,
            "client_id": config.resolved_client_id,
            "hub_api_url": config.hub_api_url,
            "agent_type": config.agent_type,
        },
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main_task = loop.create_task(_run_agent(config))

    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _create_shutdown_handler(main_task), sig, None)

    try:
        loop.run_until_complete(main_task)
    except asyncio.CancelledError:
        logger.info("agent_shutdown_complete")
    finally:
        loop.close()
        sys.exit(0)


if __name__ == "__main__":
    main()
