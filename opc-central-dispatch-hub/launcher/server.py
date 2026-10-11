"""uvicorn 服务线程管理（ADR-021）。

uvicorn.Server 在后台线程运行，主线程交给 pywebview 窗口循环；
窗口关闭后通过 ``should_exit`` 优雅停止。
"""

import logging
import threading
import time

import uvicorn

logger = logging.getLogger(__name__)

READY_TIMEOUT_SECONDS = 30
READY_POLL_INTERVAL_SECONDS = 0.5


class UvicornServer:
    """后台线程运行的 uvicorn 服务。"""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """启动服务线程（非阻塞）。

        延迟 import 并直接传入 app 实例：frozen 环境下 "main:app" 字符串
        导入不可用（main 模块未打包进 PYZ），且必须在环境变量注入之后
        构造应用（get_settings 缓存依赖注入后的 DATABASE_URL）。
        """
        from app.main import create_app

        app = create_app()
        config = uvicorn.Config(
            app,
            host=self._host,
            port=self._port,
            log_level="info",
            # 禁用 uvicorn 自带日志 dictConfig：它会把 stderr StreamHandler 挂回
            # uvicorn.access（覆盖 create_app 的桥接配置）；统一由 root FileHandler 输出
            log_config=None,
            # 桌面模式关闭 reload/多 worker：随窗口生命周期运行
            reload=False,
            workers=1,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="uvicorn-server", daemon=True)
        self._thread.start()
        logger.info("desktop_server_starting", extra={"host": self._host, "port": self._port})

    def wait_ready(self, timeout: int = READY_TIMEOUT_SECONDS) -> None:
        """等待服务真正就绪（以 uvicorn 内部 started 状态为准）。

        不能只探测 HTTP 端口：探测与 bind 之间存在竞态，端口被其他
        进程抢占时 uvicorn bind 失败退出，HTTP 探测会打到占用者的
        服务上误判就绪（窗口加载到别家页面显示空白）。uvicorn 仅在
        自身 bind + lifespan 启动成功后才置 started=True；bind 失败
        时服务线程很快退出，据此快速失败而非干等超时。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server is not None and self._server.started:
                logger.info(
                    "desktop_server_ready",
                    extra={"host": self._host, "port": self._port},
                )
                return
            if (
                self._thread is not None
                and not self._thread.is_alive()
                and not (self._server is not None and self._server.started)
            ):
                raise RuntimeError(
                    f"后端服务启动失败：端口 {self._port} 可能刚被其他程序占用，"
                    f"请重新启动（会自动改用其他空闲端口）"
                )
            time.sleep(READY_POLL_INTERVAL_SECONDS)
        raise RuntimeError(
            f"后端服务在 {timeout} 秒内未就绪（端口 {self._port}），请重新启动"
        )

    def stop(self) -> None:
        """请求优雅退出并等待线程结束。"""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
        logger.info("desktop_server_stopped")
