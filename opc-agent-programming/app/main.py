"""Linux 编程 Agent 入口。

流程（断线不退进程，应用内自动重连，见 ADR-020）：
1. 加载配置。
2. 启动连接会话循环：向中枢注册并建立 WebSocket（中枢离线时按
   reconnect_interval_seconds 间隔重试，不退出进程），心跳上报 starting 状态。
3. 并行启动 tilacode server 并等待健康，装配任务桥接层。
4. 桥接就绪后置 bridge_ready 事件，会话回调上报 idle 状态，中枢才开始派发任务。
5. 捕获 Ctrl+C / SIGTERM，按 后台任务 → 桥接 → 客户端 → tilacode 顺序优雅关闭。
"""

import asyncio
import logging
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import NoReturn

from app.client.hub_client import HubClient
from app.client.notify_server import NotifyServer
from app.config import APP_VERSION, AgentConfig
from app.executor.session_store import SessionStore, TaskMappingStore
from app.executor.task_bridge import BridgeCallbacks, TaskBridge
from app.executor.tilacode_client import TilacodeClient
from app.executor.tilacode_manager import TilacodeManager
from app.executor.workspace import WorkspaceManager
from app.utils.logger import setup_logging

# 会话登记簿文件路径（相对运行目录；升级流程必须保留，见 ADR-012）
_SESSION_STORE_PATH = Path("data/sessions.json")
# 任务派发映射持久化（task_id → project_key/output_dir；Agent 重启后
# 产物按需拉取仍能反查工作目录，2026-09-23）
_TASK_MAPPING_STORE_PATH = Path("data/task_mappings.json")


async def _build_task_bridge(
    config: AgentConfig, client: HubClient, tmgr: TilacodeManager
) -> TaskBridge:
    """基于已就绪的 tilacode server 装配任务桥接层。

    Args:
        config: Agent 配置。
        client: 中枢通信客户端（提供进度/完成/失败上报回调）。
        tmgr: tilacode 进程管理器（提供实际端口与 Basic Auth 密码）。

    Returns:
        装配完成的 TaskBridge 实例。
    """
    tilacode_client = TilacodeClient(
        # ADR-055 P2-11：URL 用回调动态解析——托管进程重启后实际端口可能变化，
        # 固化构造期快照会让重启后的所有请求打到旧端口
        base_url=lambda: f"http://{config.tilacode_host}:{tmgr.resolved_port}",
        # 密码动态取值：tilacode 崩溃自动重启会更换随机密码，
        # 固化旧密码将导致重启后所有请求 401
        password_provider=lambda: tmgr.server_password,
        directory=config.tilacode_workspace_root,
    )
    callbacks = BridgeCallbacks(
        on_progress=client.report_progress,
        on_task_started=client.report_task_started,
        on_completed=client.report_completed,
        on_failed=client.report_failed,
        on_file_tree=client.report_file_tree,
        on_file_chunk=client.send_file_chunk,
        on_file_error=client.report_file_error,
        # 验收阻断回调（ADR-061 M4）
        on_verify_blocked=client.report_verify_blocked,
    )
    return TaskBridge(
        config,
        client=tilacode_client,
        store=SessionStore(_SESSION_STORE_PATH),
        workspace=WorkspaceManager(
            config.tilacode_workspace_root,
            config_dir=config.tilacode_config_dir,
        ),
        callbacks=callbacks,
        mapping_store=TaskMappingStore(_TASK_MAPPING_STORE_PATH),
    )


async def _shutdown(
    background_tasks: list[asyncio.Task[None]],
    bridge: TaskBridge | None,
    client: HubClient,
    tmgr: TilacodeManager,
    notify_server: NotifyServer | None = None,
) -> None:
    """优雅关闭：后台任务 → 通知服务 → 桥接（含执行中任务尽力中断）→ 客户端 → tilacode。"""
    for task in background_tasks:
        task.cancel()
    if background_tasks:
        await asyncio.gather(*background_tasks, return_exceptions=True)
    if notify_server is not None:
        notify_server.stop()
    if bridge is not None:
        await bridge.shutdown()
    await client.close()
    await tmgr.stop()


async def _run_agent(config: AgentConfig) -> None:
    """运行 Agent 主循环，直到收到停止信号（断线不退进程，应用内自动重连）。

    启动顺序：先启动连接会话循环（注册 + WebSocket，中枢离线时按
    ``reconnect_interval_seconds`` 间隔重试），并行启动 tilacode；桥接装配
    完成置 ``bridge_ready`` 后会话回调才上报 idle，此时中枢才会派发任务
    （桥接未就绪时派发会被 ``task_bridge is None`` 检查拒绝，天然安全）。
    """
    client = HubClient(config)
    tmgr = TilacodeManager(config, logging.getLogger("app.executor"))
    bridge: TaskBridge | None = None
    background_tasks: list[asyncio.Task[None]] = []
    # tilacode 就绪事件：会话回调据此等待桥接装配完成后再上报 idle
    bridge_ready = asyncio.Event()
    # 通知 API 服务（ADR-035）：配置了 API_TOKEN 才启动（避免无认证端点暴露）；
    # 启动失败（端口占用/绑定地址失效等）仅告警降级，不影响 Agent 主流程
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
        """每次连接建立后的就绪回调：等待桥接就绪后恢复状态上报。

        首连时 bridge_ready 未置位则在此等待（心跳已上报 starting，中枢不会
        派发任务）；重连时事件已置位立即返回，恢复 busy/idle 并补报缓冲事件。
        若 tilacode 已进入不可用状态（连续重启失败超上限），则保持不可用
        状态上报——中枢据此不派发任务（ADR-055 P2-11）。
        """
        await bridge_ready.wait()
        await client.restore_session_state()
        if tmgr.is_unavailable:
            await client.update_status("unavailable")

    async def on_tilacode_exit(reason: str) -> None:
        """tilacode 异常退出回调：使在跑任务失败，并同步节点可用性（ADR-055）。

        - 重启成功：恢复 ``bridge_ready`` 并上报 idle，中枢可继续派发；
        - 重启失败/超上限：清除 ``bridge_ready``（派发会被拒绝）并上报
          unavailable，避免中枢继续把任务派到无法执行的节点上。
        """
        if bridge is not None:
            await bridge.handle_tilacode_crash(reason)
        if tmgr.is_unavailable:
            bridge_ready.clear()
            await client.update_status("unavailable")
            return
        bridge_ready.set()
        await client.update_status("idle")

    try:
        # 1. 启动重连会话循环（立即注册建连；中枢离线时按间隔重试，不退进程）
        background_tasks.append(asyncio.create_task(client.run_forever(on_session_ready)))

        # 2. 启动前自检：清理用户级 tilacode 配置中的未知顶层键（2026-09-27 事故防线）。
        #    tilacode 对配置做严格校验，顶层未知键会让**整个配置文件失效**，
        #    表现为「服务启动即退出重启循环」或「任务永远停在 10% 无事件」。
        #    历史版本残留（providers/_hub_managed_providers）即由此造成，故每次
        #    启动前主动清理一次；清理失败不阻断启动（仅在日志中告警）。
        try:
            removed = WorkspaceManager(
                config.tilacode_workspace_root,
                config_dir=config.tilacode_config_dir,
            ).sanitize_user_config()
            if removed:
                logging.getLogger(__name__).warning(
                    "tilacode_user_config_sanitized",
                    extra={"event": "tilacode_user_config_sanitized", "removed_keys": removed},
                )
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "tilacode_user_config_sanitize_failed",
                extra={"error": str(exc)},
            )

        # 3. 并行启动 tilacode 并等待健康，装配任务桥接层与守护任务
        await tmgr.start()
        await tmgr.wait_healthy()
        bridge = await _build_task_bridge(config, client, tmgr)
        client.task_bridge = bridge
        background_tasks.append(
            asyncio.create_task(tmgr.monitor(on_unexpected_exit=on_tilacode_exit))
        )
        # ADR-055 P2-11：落地周期性健康检查（消费 TILACODE_HEALTH_CHECK_INTERVAL）
        background_tasks.append(asyncio.create_task(tmgr.health_monitor()))

        # 4. tilacode 就绪：触发会话回调上报 idle，此时中枢才会派发任务
        bridge_ready.set()

        # 5. 等待会话循环与守护任务（断线重连在 run_forever 内部完成）
        await asyncio.gather(*background_tasks)
    except asyncio.CancelledError:
        logging.info("agent_run_cancelled")
        raise
    finally:
        await _shutdown(background_tasks, bridge, client, tmgr, notify_server)


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
