"""桌面模式主流程编排（ADR-021）。

双击启动 → 单实例锁 → 端口探测 → 内嵌 PG → 环境注入 →
迁移/初始数据 → uvicorn 后台线程 → pywebview 窗口 → 关窗清理退出。
"""

import ctypes
import logging
import os
import secrets
import sys
import threading
import time
from ctypes import wintypes
from typing import Any

from launcher import paths
from launcher.db_setup import setup_database
from launcher.errors import AlreadyRunningError, LauncherError, PortExhaustedError
from launcher.instance_lock import singleton_lock
from launcher.pg_manager import EmbeddedPostgres
from launcher.port import find_free_port, is_port_free
from launcher.server import UvicornServer

logger = logging.getLogger("launcher")

# 初始窗口逻辑尺寸（create_window 与还原态换算共用）
INITIAL_WINDOW_WIDTH = 1280
INITIAL_WINDOW_HEIGHT = 860


class DesktopStack:
    """已启动组件栈引用集合（server + 内嵌 PG），统一停止。"""

    def __init__(self, server: UvicornServer, pg: EmbeddedPostgres) -> None:
        self.server = server
        self.pg = pg

    def stop(self) -> None:
        """停止后端服务与内嵌数据库（幂等）。"""
        self.server.stop()
        self.pg.stop()
        logger.info("desktop_stack_stopped")


def _setup_logging() -> None:
    """launcher 阶段日志（应用日志由 create_app 内 configure_logging 接管）。

    幂等：Alembic 的 fileConfig 会替换 root handlers（迁移后 launcher 日志
    静默丢失的根因），因此每次调用先清空 root handlers 再挂 FileHandler。
    """
    log_path = paths.log_dir()
    log_path.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    handler = logging.FileHandler(log_path / "launcher.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _show_error(message: str) -> None:
    """展示错误信息：写日志 + stderr（无窗口环境可查）+ 弹窗。"""
    logger.error("desktop_error", extra={"error_message": message})
    if sys.stderr is not None:  # console=False 打包时 stderr 为 None
        print(f"[ERROR] {message}", file=sys.stderr, flush=True)
    ctypes.windll.user32.MessageBoxW(None, message, "TilapiaHub 启动失败", 0x10)


def _guard_null_streams() -> None:
    """console=False 打包时 stdout/stderr 为 None，重定向避免任何 print 崩溃。"""
    if sys.stdout is None:
        sys.stdout = open(  # noqa: SIM115 生命周期与进程一致
            paths.log_dir() / "stdout.log", "w", encoding="utf-8"
        )
    if sys.stderr is None:
        sys.stderr = open(  # noqa: SIM115 生命周期与进程一致
            paths.log_dir() / "stderr.log", "w", encoding="utf-8"
        )


# ADR-031 端口耗尽兜底页：内嵌 HTML（后端未启动，无法加载正式前端），
# `__REASON__` 占位符在创建窗口时替换为实际原因；经 js_api 调 start_backend。
_FALLBACK_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  body { margin: 0; font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
         background: #1a1b1e; color: #e2e4f2; display: flex;
         align-items: center; justify-content: center; height: 100vh; }
  .card { width: 480px; background: #222427; border: 1px solid rgba(224,226,242,.12);
          border-radius: 10px; padding: 28px 32px; }
  h1 { font-size: 18px; margin: 0 0 12px; color: #4ade80; }
  p  { font-size: 13px; line-height: 1.8; color: rgba(224,226,242,.72); margin: 0 0 16px; }
  .reason { font-size: 12px; color: rgba(246,90,90,.9); margin-bottom: 16px;
            word-break: break-all; }
  .row { display: flex; gap: 10px; }
  input { flex: 1; padding: 9px 12px; border-radius: 6px; font-size: 14px;
          border: 1px solid rgba(224,226,242,.18); background: #1a1b1e; color: #e2e4f2; }
  button { padding: 9px 20px; border: none; border-radius: 6px; font-size: 14px;
           background: #4ade80; color: #052e12; cursor: pointer; font-weight: 600; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  #status { margin-top: 14px; font-size: 12px; color: rgba(224,226,242,.55);
            min-height: 18px; white-space: pre-wrap; }
</style>
</head>
<body>
  <div class="card">
    <h1>后端服务未能自动启动</h1>
    <p>系统尝试在 8000-8010 范围内自动选择后端端口，但所有端口均被其他程序占用。
       请手动指定一个可用端口（建议 8011-8100），后端将以该端口启动，
       子 Agent 也会在重连时自动探测到新端口。</p>
    <div class="reason">原因：__REASON__</div>
    <div class="row">
      <input id="port" type="number" min="1" max="65535" value="8011" placeholder="后端端口">
      <button id="btn" onclick="start()">启动后端</button>
    </div>
    <div id="status"></div>
  </div>
  <script>
    async function start() {
      const port = document.getElementById('port').value;
      const btn = document.getElementById('btn');
      const status = document.getElementById('status');
      btn.disabled = true;
      status.textContent = '正在启动后端（含内嵌数据库初始化，可能需要数分钟）…';
      try {
        const result = await window.pywebview.api.start_backend(Number(port));
        if (result.status === 'ok') {
          status.textContent = '后端已启动，正在打开控制台…';
        } else {
          status.textContent = result.message;
          btn.disabled = false;
        }
      } catch (e) {
        status.textContent = '调用失败：' + e;
        btn.disabled = false;
      }
    }
  </script>
</body>
</html>
"""


def _inject_env(port: int, database_url: str) -> None:
    """注入桌面模式环境变量（优先级高于 .env.local，且不读写 .env 文件）。"""
    secret_key = secrets.token_urlsafe(32)
    os.environ.update(
        APP_ENV="desktop",
        DEBUG="false",
        LOG_LEVEL="INFO",
        LOG_DIR=str(paths.log_dir()),
        # 0.0.0.0：允许局域网 Agent 节点接入（项目约定，与源码模式一致）；
        # 内嵌数据库仍仅监听 127.0.0.1，不对外暴露
        HOST="0.0.0.0",
        PORT=str(port),
        WORKERS="1",
        DATABASE_URL=database_url,
        # Redis 为名义依赖（代码零调用），注入占位值满足必填校验
        REDIS_URL="redis://127.0.0.1:15439/0",
        SECRET_KEY=secret_key,
        AGENT_REGISTRATION_TOKEN=secrets.token_urlsafe(24),
        CORS_ORIGINS=f"http://127.0.0.1:{port}",
        # ADR-022：资源缓存目录（运行期数据）与云端检查间隔
        RESOURCE_CACHE_DIR=str(paths.data_dir() / "resources"),
        RESOURCE_SYNC_INTERVAL_SECONDS="21600",
    )


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _workarea_of(hwnd: int) -> wintypes.RECT | None:
    """取窗口所在显示器的工作区（不含任务栏）；失败返回 None。"""
    user32 = ctypes.windll.user32
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    hmon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    if not hmon or not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        return None
    return info.rcWork


def _fill_workarea(hwnd: int) -> None:
    """把窗口铺满所在显示器的工作区（最大化形态，不覆盖任务栏）。

    无边框窗口（WS_CAPTION 已移除）经 WinForms/Win32 二次最大化会
    覆盖任务栏（无边框最大化边界计算不含工作区修正），故手动定位到
    工作区替代系统最大化。
    """
    wa = _workarea_of(hwnd)
    if wa is None:
        return
    ctypes.windll.user32.SetWindowPos(
        hwnd, 0, wa.left, wa.top,
        wa.right - wa.left, wa.bottom - wa.top,
        0x0004,  # SWP_NOZORDER
    )


def _restore_window_rect(hwnd: int, restore_rect: wintypes.RECT | None) -> None:
    """还原窗口到常规尺寸：优先回到记住的位置，否则按初始尺寸居中工作区。

    启动流程的 SW_RESTORE 已把 WinForms 窗口状态置为 Normal，pywebview
    的 restore() 再设 Normal 是无操作（窗口停在铺满态不会还原），因此
    还原必须显式 SetWindowPos 定位。
    """
    if restore_rect is not None:
        ctypes.windll.user32.SetWindowPos(
            hwnd, 0,
            restore_rect.left, restore_rect.top,
            restore_rect.right - restore_rect.left,
            restore_rect.bottom - restore_rect.top,
            0x0004,  # SWP_NOZORDER
        )
        return
    wa = _workarea_of(hwnd)
    if wa is None:
        return
    dpi = ctypes.windll.user32.GetDpiForWindow(hwnd) or 96
    width = int(INITIAL_WINDOW_WIDTH * dpi / 96)
    height = int(INITIAL_WINDOW_HEIGHT * dpi / 96)
    x = wa.left + ((wa.right - wa.left) - width) // 2
    y = wa.top + ((wa.bottom - wa.top) - height) // 2
    ctypes.windll.user32.SetWindowPos(hwnd, 0, x, y, width, height, 0x0004)


def _tune_window_chrome() -> None:
    """后台线程定位窗口句柄，去除系统标题栏但保留可调边框。

    常规窗口样式含 WS_CAPTION（标题栏）+ WS_THICKFRAME（可调边框）：
    仅清除 WS_CAPTION 并保留 WS_THICKFRAME，即可实现——页面独占窗口
    （无标题栏）、鼠标置于窗口边缘可原生拖拽缩放、Aero Snap（Win+方向键）
    与拖到屏幕边缘分屏均可用，最大化不覆盖任务栏。改动需 SetWindowPos
    FRAMECHANGED 强制重算非客户区后生效。
    按窗口标题用 FindWindowW 定位句柄（不依赖 pywebview 内部属性）；
    窗口出现前 50ms 间隔快速轮询（避免标题栏闪现），超时自动放弃
    （纯美化，不影响主流程）。
    """
    GWL_STYLE = -16
    WS_CAPTION = 0x00C00000
    GWL_EXSTYLE = -20
    WS_EX_APPWINDOW = 0x00040000
    SWP_FRAMECHANGED_FLAGS = 0x0027  # NOSIZE | NOMOVE | NOZORDER | FRAMECHANGED
    SW_RESTORE = 9

    # 轮询窗口要覆盖 PG 初始化+迁移的耗时（首启可达数分钟）：前 60 秒
    # 50ms 快速轮询（窗口一出现立即去标题栏），之后 1s 间隔直至超时
    deadline = time.monotonic() + 300
    fast_deadline = time.monotonic() + 60
    hwnd = 0
    while time.monotonic() < deadline:
        hwnd = ctypes.windll.user32.FindWindowW(None, "OPC 中枢调度台")
        if hwnd:
            break
        time.sleep(0.05 if time.monotonic() < fast_deadline else 1)
    if not hwnd:
        logger.warning("desktop_window_chrome_hwnd_not_found")
        return
    try:
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(hwnd, GWL_STYLE, style & ~WS_CAPTION)
        # Windows 规则：无 WS_CAPTION 的顶级窗口默认不进任务栏，
        # 显式加 WS_EX_APPWINDOW 强制显示任务栏按钮（含图标与预览）
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_APPWINDOW)
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FRAMECHANGED_FLAGS)
        # 重样式变更会中断 WinForms 布局，WebView2 子窗口停留在隐藏态
        # （页面 API 正常请求但窗口只剩背景色）：SW_RESTORE 重走一次完整
        # 布局流程恢复子窗口显示；还原是异步的，等 WinForms 处理完
        # （否则其 RestoreBounds 会覆盖定位）再铺满工作区——无边框窗口
        # 的系统二次最大化会覆盖任务栏，故手动定位到工作区
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.3)
        _fill_workarea(hwnd)
        logger.info("desktop_window_chrome_applied", extra={"hwnd": hwnd})
    except Exception:  # noqa: BLE001 美化失败不影响功能
        logger.warning("desktop_window_chrome_failed", exc_info=True)


class DesktopApi:
    """暴露给前端 JS 的桌面壳桥（ADR-028 文件夹选择 + 无边框窗口控制）。

    浏览器环境无法取得本机绝对路径，仅桌面壳（pywebview）提供原生
    「选择文件夹」对话框能力；无标题栏窗口的拖动/最大化由前端页面
    顶部区域经 ``window.pywebview.api`` 调用。
    """

    # 主窗口标题（FindWindowW 定位句柄用，与 create_window 的 title 一致）
    _WINDOW_TITLE = "OPC 中枢调度台"

    def __init__(self) -> None:
        self._window = None
        # 最大化（铺满工作区）前的窗口矩形，还原时恢复到原位置
        self._restore_rect: wintypes.RECT | None = None

    def bind(self, window: object) -> None:
        """窗口创建后注入引用（create_file_dialog 需窗口实例）。"""
        self._window = window

    def select_folder(self) -> str | None:
        """打开原生文件夹选择对话框，返回所选绝对路径；取消返回 None。"""
        if self._window is None:
            return None
        import webview

        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if isinstance(result, (list, tuple)) and result:
            return str(result[0])
        return None

    def begin_move_window(self) -> None:
        """拖动主窗口（前端页面顶部区域 mousedown 时调用）。

        WebView2 持有 Win32 鼠标捕获，HTCAPTION 消息方案收不到移动
        消息会失效；改为循环跟随：记录按下时光标与窗口的偏移，此后
        每帧把窗口定位到"光标 - 偏移"，直到左键释放（GetAsyncKeyState）。
        拖动最大化（铺满工作区）窗口时先还原到常规尺寸，符合标准
        窗口行为。
        """
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, self._WINDOW_TITLE)
        if not hwnd:
            return
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        wa = _workarea_of(hwnd)
        filled = wa is not None and (
            rect.left <= wa.left
            and rect.top <= wa.top
            and rect.right >= wa.right
            and rect.bottom >= wa.bottom
        )
        if filled and self._window is not None:
            _restore_window_rect(hwnd, self._restore_rect)
            time.sleep(0.3)  # 等待 WinForms 完成布局再取新尺寸
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        off_x = pt.x - rect.left
        off_y = pt.y - rect.top
        # SWP_NOSIZE | SWP_NOZORDER：只移动不改变尺寸与层级
        flags = 0x0001 | 0x0004
        while user32.GetAsyncKeyState(0x01) & 0x8000:  # VK_LBUTTON 按住
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetWindowPos(
                hwnd, 0, pt.x - off_x, pt.y - off_y, 0, 0, flags
            )
            time.sleep(0.016)  # ~60fps 跟随

    def minimize_window(self) -> None:
        """最小化主窗口（右上角窗口控制按钮调用）。"""
        if self._window is not None:
            self._window.minimize()

    def close_window(self) -> None:
        """关闭主窗口（触发 FormClosed → 组件栈统一清理退出）。"""
        if self._window is not None:
            self._window.destroy()

    def toggle_maximize_window(self) -> None:
        """最大化/还原主窗口（按窗口是否已铺满工作区判断，双击顶部区域同效）。

        还原走显式 SetWindowPos 定位（restore() 在 WinForms Normal 态
        下是无操作，见 _restore_window_rect 注释）。
        """
        if self._window is None:
            return
        hwnd = ctypes.windll.user32.FindWindowW(None, self._WINDOW_TITLE)
        if not hwnd:
            return
        wa = _workarea_of(hwnd)
        if wa is None:
            return
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        filled = (
            rect.left <= wa.left
            and rect.top <= wa.top
            and rect.right >= wa.right
            and rect.bottom >= wa.bottom
        )
        if filled:
            _restore_window_rect(hwnd, self._restore_rect)
        else:
            self._restore_rect = wintypes.RECT(
                rect.left, rect.top, rect.right, rect.bottom
            )
            _fill_workarea(hwnd)


def _icon_path() -> str | None:
    """应用图标路径（assets/tilapia-icon.ico）；缺失时返回 None 走默认提取。"""
    icon = paths.resource_root() / "assets" / "tilapia-icon.ico"
    return str(icon) if icon.is_file() else None


def _open_window(url: str) -> None:
    """创建桌面窗口并阻塞至关闭（WebView2 渲染，无边框最大化启动）。

    以常规窗口 + maximized 启动（不覆盖任务栏），后台线程
    `_tune_window_chrome` 窗口一出现即移除标题栏——保留可调边框，
    页面独占窗口且边缘可原生拖拽缩放；窗口拖动/双击最大化由前端
    顶部区域 + DesktopApi 提供。
    """
    try:
        import webview
    except ImportError as exc:
        raise LauncherError("缺少 pywebview 组件，安装不完整") from exc
    desktop_api = DesktopApi()
    window = webview.create_window(
        DesktopApi._WINDOW_TITLE,
        url,
        width=INITIAL_WINDOW_WIDTH,
        height=INITIAL_WINDOW_HEIGHT,
        min_size=(1024, 700),
        maximized=True,
        background_color="#1a1b1e",  # 加载期与页面深色主题一致，避免白屏闪烁
        js_api=desktop_api,
    )
    desktop_api.bind(window)
    threading.Thread(target=_tune_window_chrome, daemon=True).start()
    try:
        webview.start(gui="edgechromium", icon=_icon_path())
    except Exception as exc:
        raise LauncherError(
            f"桌面窗口创建失败（{exc}）。请确认已安装 Microsoft Edge WebView2 Runtime。"
        ) from exc
    del window


def run_desktop() -> None:
    """桌面模式入口：完整启动流程与错误兜底。"""
    _setup_logging()
    try:
        _run()
    except AlreadyRunningError as exc:
        _show_error(str(exc))
        sys.exit(2)
    except LauncherError as exc:
        logger.error("desktop_error", extra={"error_message": str(exc)}, exc_info=True)
        _show_error(str(exc))
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 兜底入口：所有未预期异常均弹窗告知
        logger.error("desktop_unexpected_error", extra={"error_message": str(exc)}, exc_info=True)
        _show_error(f"启动过程中发生未预期的错误：{exc}")
        sys.exit(1)


def _run() -> None:
    """实际启动序列（异常交由 run_desktop 统一处理）。"""
    _guard_null_streams()
    os.chdir(paths.resource_root())  # 相对路径基准：static/、alembic.ini 等以资源根为 CWD
    data = paths.data_dir()
    data.mkdir(parents=True, exist_ok=True)

    with singleton_lock(data):
        # ADR-031：端口探测范围耗尽时不退出，进入兜底窗口（手动指定端口启动后端）
        try:
            port = find_free_port()
        except PortExhaustedError as exc:
            logger.error("desktop_port_exhausted", extra={"reason": str(exc)})
            _open_fallback_window(str(exc))
            return

        stack = _start_stack(port)
        try:
            _open_window(f"http://127.0.0.1:{port}")
        finally:
            stack.stop()
            logger.info("desktop_shutdown_completed")


def _start_stack(port: int) -> DesktopStack:
    """启动内嵌组件栈（PG → 环境注入 → 迁移 → 后端服务），供正常与兜底路径复用。

    Args:
        port: 后端监听端口。

    Returns:
        DesktopStack：持有 server/pg 引用，窗口关闭时统一 stop()。
    """
    pg = EmbeddedPostgres(
        pg_bin_dir=paths.pg_bin_dir(),
        pg_data_dir=paths.pg_data_dir(),
        env_file=paths.pg_env_file(),
    )
    logger.info(
        "desktop_startup_begin",
        extra={"root": str(paths.app_root()), "port": port},
    )
    server: UvicornServer | None = None
    try:
        pg_env = pg.ensure_started()
        _inject_env(port, pg_env.database_url)
        setup_database(str(paths.resource_root()))
        _setup_logging()  # alembic fileConfig 替换了 root handlers，重新挂文件日志

        server = UvicornServer(host=os.environ["HOST"], port=port)
        server.start()
        server.wait_ready()
    except Exception:
        if server is not None:
            server.stop()
        pg.stop()
        raise
    return DesktopStack(server=server, pg=pg)


def _open_fallback_window(reason: str) -> None:
    """端口耗尽兜底窗口（ADR-031）：UI 不退出，手动指定端口启动后端。

    展示避让范围耗尽的原因与引导；用户填入可用端口点击「启动后端」后，
    以该端口拉起组件栈并跳转正常界面。
    """
    import webview

    api = FallbackApi(reason)
    window = webview.create_window(
        "OPC 中枢调度台",
        html=_FALLBACK_HTML.replace("__REASON__", reason),
        width=720,
        height=480,
        min_size=(560, 420),
        js_api=api,
    )
    api.bind(window)
    try:
        webview.start(gui="edgechromium", icon=_icon_path())
    finally:
        stack = api.stack
        if stack is not None:
            stack.stop()
            logger.info("desktop_fallback_stack_stopped")


class FallbackApi:
    """兜底窗口 JS 桥：以用户指定端口启动后端组件栈（ADR-031）。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self._window: Any | None = None
        self.stack: DesktopStack | None = None

    def bind(self, window: Any) -> None:
        """窗口创建后注入引用（load_url 跳转需要窗口实例）。"""
        self._window = window

    def start_backend(self, port: int) -> dict[str, str]:
        """以指定端口启动后端；成功后窗口跳转正常界面。

        Args:
            port: 用户输入的后端端口（1-65535）。

        Returns:
            {status: ok} 或 {status: error, message: 原因}。
        """
        try:
            port_num = int(port)
        except (TypeError, ValueError):
            return {"status": "error", "message": "端口必须是数字"}
        if not (1 <= port_num <= 65535):
            return {"status": "error", "message": "端口范围为 1-65535"}
        if not is_port_free(port_num):
            return {"status": "error", "message": f"端口 {port_num} 也被占用，请换一个"}
        if self.stack is not None:
            return {"status": "error", "message": "后端已在运行"}

        try:
            self.stack = _start_stack(port_num)
        except Exception as exc:  # noqa: BLE001 兜底路径：启动失败原因回显给用户
            self.stack = None
            logger.error("desktop_fallback_start_failed", extra={"error": str(exc)})
            return {"status": "error", "message": f"后端启动失败：{exc}"}

        if self._window is not None:
            self._window.load_url(f"http://127.0.0.1:{port_num}")
        logger.info("desktop_fallback_backend_started", extra={"port": port_num})
        return {"status": "ok"}
