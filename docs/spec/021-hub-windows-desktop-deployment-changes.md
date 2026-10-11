# 中枢 Windows 桌面一体化部署 Changes 变更清单

关联 ADR：[ADR-021](../adr/021-hub-windows-desktop-deployment.md)
关联 Spec：[021-hub-windows-desktop-deployment-spec.md](./021-hub-windows-desktop-deployment-spec.md)
关联 Harness：[021-hub-windows-desktop-deployment-harness.md](./021-hub-windows-desktop-deployment-harness.md)

> 按 `.trae/rules/decision-documents.md` 要求：编码前仅创建骨架，开发过程中**每修改/新增/删除一个文件立即登记**，不得事后一次性补记。

## 变更清单

| 日期 | 文件路径 | 变更类型（修/增/减） | 函数/类级明细 | 作用说明 |
|------|---------|---------------------|--------------|---------|
| 2026-09-05 | docs/adr/021-hub-windows-desktop-deployment.md | 增 | - | ADR 决策文档 |
| 2026-09-05 | docs/spec/021-hub-windows-desktop-deployment-spec.md | 增 | - | Spec |
| 2026-09-05 | docs/spec/021-hub-windows-desktop-deployment-harness.md | 增 | - | Harness |
| 2026-09-05 | docs/spec/021-hub-windows-desktop-deployment-changes.md | 增 | - | Changes 骨架 |
| 2026-09-05 | docs/INDEX.md | 修 | 1.1/1.2/1.3/六 登记 | 决策文档索引 |
| 2026-09-05 | opc-central-dispatch-hub/app/core/static_spa.py | 增 | `resolve_web_dist_dir()` / `mount_web_console(app, web_dir)`（SPA fallback catch-all，排除 api//ws/ 前缀，防目录穿越） | 前端静态产物托管 + history 路由回退（ADR-021 决策 2） |
| 2026-09-05 | opc-central-dispatch-hub/app/main.py | 修 | `create_app()`：根路径改为条件注册（web 存在→`mount_web_console`，否则保留原 JSON root）；新增 import `mount_web_console/resolve_web_dist_dir` | web/ 产物存在时根路径返回前端页面，纯后端行为不变 |
| 2026-09-05 | opc-central-dispatch-hub/launcher/errors.py | 增 | `LauncherError` / `AlreadyRunningError` / `PortExhaustedError` / `PgError` / `MigrationError` | launcher 异常层级（coding-standards §6） |
| 2026-09-05 | opc-central-dispatch-hub/launcher/paths.py | 增 | `app_root()` / `data_dir()` / `pg_data_dir()` / `pg_bin_dir()` / `log_dir()` / `pg_env_file()` | frozen 与源码双形态路径定位 |
| 2026-09-05 | opc-central-dispatch-hub/launcher/port.py | 增 | `is_port_free()` / `find_free_port(first=8000, last=8010)` | HTTP 端口自动探测（Spec §3.1） |
| 2026-09-05 | opc-central-dispatch-hub/launcher/instance_lock.py | 增 | `singleton_lock(data_dir)`（Windows 命名互斥体 + 文件锁回退） | 单实例锁（Spec §4 二次启动降级） |
| 2026-09-05 | opc-central-dispatch-hub/launcher/pg_manager.py | 增 | `PgEnv`（含 database_url 属性）/ `generate_password()` / `EmbeddedPostgres.ensure_started/_initdb/_start/_wait_ready/_ensure_database/stop` | 内嵌 PG 便携版生命周期：initdb（scram-sha-256+UTF8）、pg_ctl 启停（127.0.0.1:15432）、连接信息落盘 pg_env.json |
| 2026-09-05 | opc-central-dispatch-hub/launcher/db_setup.py | 增 | `run_migrations(app_root)`（进程内 alembic API）/ `seed_initial_data()`（幂等插入 v0.1.0）/ `setup_database()` | 首次启动自动迁移 + 初始版本数据（对齐 ADR-018） |
| 2026-09-05 | opc-central-dispatch-hub/launcher/server.py | 增 | `UvicornServer.start/wait_ready/stop`（后台线程 + /docs 就绪轮询 + should_exit 优雅退出） | uvicorn 服务线程管理 |
| 2026-09-05 | opc-central-dispatch-hub/launcher/desktop.py | 增 | `run_desktop()`（错误兜底弹窗）/ `_run()`（启动序列编排）/ `_inject_env()` / `_open_window()`（pywebview WebView2）/ `_show_error()` | 桌面模式主流程：锁→端口→PG→环境注入→迁移→服务→窗口→关窗清理 |
| 2026-09-05 | opc-central-dispatch-hub/launcher/__init__.py / __main__.py | 增 | 包说明 / `python -m launcher` 入口 | launcher 包与入口 |
| 2026-09-05 | opc-central-dispatch-hub/pyproject.toml | 修 | `[project.optional-dependencies]` 新增 `desktop = [pywebview, pyinstaller]` | 桌面形态可选依赖，不影响 dev 依赖 |
| 2026-09-05 | opc-central-dispatch-hub/tests/api/test_static_spa.py | 增 | IT-1~IT-5 用例（根路径/SPA 回退/API 优先/assets/无 web 降级） | 静态托管集成测试 |
| 2026-09-05 | opc-central-dispatch-hub/tests/launcher/（test_port / test_pg_manager / test_paths / test_instance_lock / test_startup_flow + __init__） | 增 | UT-1~UT-6 + IT-6 用例 | launcher 单元与编排测试 |
| 2026-09-05 | opc-central-dispatch-hub/scripts/copy_web_dist.py | 增 | `main()`：dist → web 复制（清空旧产物） | 前端产物复制脚本 |
| 2026-09-05 | opc-central-dispatch-hub/scripts/prepare_desktop_bundle.py | 增 | `download()` / `main()`（幂等，支持 --zip 离线） | PostgreSQL 便携版下载解压 |
| 2026-09-05 | opc-central-dispatch-hub/launcher/tilapia-hub.spec | 增 | Analysis/PYZ/EXE/COLLECT（datas：alembic、static、assets、web；hiddenimports：uvicorn 子模块、alembic、app.models） | PyInstaller onedir 打包配置 |
| 2026-09-05 | opc-central-dispatch-hub/installer/tilapia-hub.iss | 增 | Setup/Tasks/Files/Icons/Run/UninstallDelete 段 | Inno Setup 安装向导（lowest 权限、LOCALAPPDATA 安装、数据随卸载清除） |
| 2026-09-05 | .gitignore | 修 | 追加 data/、web/、pgsql/、build/、dist/、installer/Output/ 排除规则 | 桌面版运行期数据与打包产物不入库 |
| 2026-09-05 | opc-central-dispatch-hub/launcher/pg_manager.py | 修 | `_start`/`stop` 输出由 capture_output 管道改为文件重定向；新增 `_run_quiet` | 修复 pg_ctl start 拉起的 postmaster 继承管道句柄不关闭导致 subprocess.run 永久卡死（Windows 经典坑） |
| 2026-09-05 | opc-central-dispatch-hub/launcher/db_setup.py | 修 | app 模块 import 全部延迟到函数内部；`seed_initial_data` 结束时 `engine.dispose()` | 修复 get_settings() 缓存早于环境注入（连错 .env.local 远程库）与连接池跨事件循环复用（Event loop is closed）两个根因 |
| 2026-09-05 | opc-central-dispatch-hub/launcher/desktop.py | 修 | `_run` 整体 try/finally 清理（server 可选）；`_show_error` 补 stderr 输出；日志 extra 保留字段 message→error_message | 启动任一步失败均停止已启动组件；无窗口环境可从 stderr 排查 |
| 2026-09-05 | opc-central-dispatch-hub/launcher/instance_lock.py | 修 | `os.register_at_exit`→`atexit.register` | 修复互斥体句柄注册调用的 AttributeError |
| 2026-09-05 | opc-central-dispatch-hub/tests/launcher/test_startup_flow.py | 修 | 两个降级用例加 patch.dict 环境隔离；断言对齐新清理语义 | 修复 mock 假密码经 _inject_env 泄漏到 os.environ 污染后续测试（InvalidPasswordError） |
| 2026-09-05 | opc-central-dispatch-hub/scripts/verify_desktop_launcher.py | 修 | main 改同步函数（对齐 desktop._run 上下文） | 修复外层 asyncio.run 与 Alembic env.py 内层 asyncio.run 嵌套冲突 |
| 2026-09-05 | opc-central-dispatch-hub/alembic/env.py | 修 | `fileConfig(..., disable_existing_loggers=False)` | 修复 Alembic fileConfig 禁用既有 logger 导致 launcher 错误日志丢失 |
| 2026-09-05 | opc-central-dispatch-hub/launcher/server.py | 修 | `start()` 延迟 import create_app 并直接传 app 实例（不再用 "main:app" 字符串） | 修复 frozen 下 "main:app" 字符串导入失败导致 uvicorn 线程启动即退、wait_ready 30s 超时 |
| 2026-09-05 | opc-central-dispatch-hub/launcher/pg_manager.py | 修 | `_ensure_database` 的 createdb 调用注入 PGPASSWORD 环境变量 | 修复 frozen 首启建库时 scram 认证失败（源码路径因库已存在未暴露） |
| 2026-09-05 | opc-central-dispatch-hub/launcher/paths.py | 修 | 新增 `resource_root()`（frozen 时 sys._MEIPASS）；desktop 改 chdir 资源根、setup_database 传资源根 | 修复 PyInstaller onedir datas 位于 _internal/ 导致的路径错位 |
| 2026-09-05 | opc-central-dispatch-hub/launcher/desktop.py | 修 | run_desktop 两个 except 均加 exc_info=True 写入日志 | frozen 阶段错误详情可追溯 |
| 2026-09-05 | opc-central-dispatch-hub/launcher/tilapia-hub.spec | 修 | Analysis 入口改绝对路径 | 修复 spec 相对路径被解析为 spec 目录导致 "launcher\\launcher\\__main__.py not found" |
| 2026-09-05 | opc-central-dispatch-hub/scripts/import_remote_dump.py | 增 | `filter_dump`（过滤 \\restrict/\\unrestrict/SET transaction_timeout）/ `import_dump` / `verify_counts` | 远程库 PG18 dump 导入本地内嵌库（PG16）的数据镜像脚本 |
| 2026-09-05 | docs/ops/hub-desktop-build-guide.md | 增 | 两种运行形态/环境准备/桌面运行/6 步打包流程/发版测试清单/常见问题 | exe 编译发布指南（用户要求文档化） |
| 2026-09-06 | opc-central-dispatch-hub/scripts/build_desktop.py | 增 | step_stop_running/step_pgsql_ready/step_npm_build/step_copy_web/step_pyinstaller/step_link_pgsql | 一键构建脚本：封装全部打包步骤，自动停止运行实例、重建 pgsql 链接 |
| 2026-09-06 | opc-central-dispatch-hub/launcher/pg_manager.py | 修 | `ensure_started` 前置检查 initdb.exe 存在性 | 修复产物缺 pgsql 时报无意义的 WinError 2，改为明确提示 |
| 2026-09-06 | opc-central-dispatch-hub/app/core/logging.py | 修 | `SensitiveQueryMaskFilter.filter` 保持 record.args 结构、仅脱敏字符串参数 | 修复脱敏 filter 置空 args 导致 uvicorn AccessFormatter 解包失败（--- Logging error --- 刷屏，存量问题） |
| 2026-09-06 | opc-central-dispatch-hub/launcher/pg_manager.py | 修 | 新增 `_credentials_match` / `_next_free_port` / `_save_env`；`ensure_started` 端口被异实例占用时顺延端口避让 | 修复源码模式与桌面版（不同数据目录/密码）抢占 15432 导致的 InvalidPasswordError |
| 2026-09-06 | docs/ops/hub-desktop-build-guide.md | 修 | 常见问题表补充 3 条（日志文件占用/缺 pgsql/Logging error） | 一键构建使用说明与排障补充 |
| 2026-09-06 | opc-central-dispatch-hub/launcher/tilapia-hub.spec | 修 | EXE console=True→False | 去掉桌面窗口后面的黑色控制台 |
| 2026-09-06 | opc-central-dispatch-hub/launcher/desktop.py | 修 | 新增 `_guard_null_streams`（stdout/stderr 重定向）；`_show_error` stderr 防护；HOST 0.0.0.0；`UvicornServer(host=os.environ["HOST"])`；新增 `_apply_dark_titlebar`（DWMWA_USE_IMMERSIVE_DARK_MODE 深色标题栏） | 无控制台打包防 print 崩溃；局域网 Agent 接入（修安装节点失败）；标题栏跟随深色主题 |
| 2026-09-06 | opc-central-dispatch-hub/launcher/server.py | 修 | `uvicorn.Config(log_config=None)`；`wait_ready` 探测固定 127.0.0.1 回环 | uvicorn 日志统一桥接 root FileHandler；0.0.0.0 监听下探测地址可连接 |
| 2026-09-06 | opc-web-console/src/index.css | 修 | 新增 `::-webkit-scrollbar` 深色滚动条全局样式 | WebView2 默认浅色滚动条在深色界面中突兀，定制为半透明深色细条 |
| 2026-09-06 | opc-central-dispatch-hub/launcher/pg_manager.py | 修 | 全部 subprocess.run 增加 `CREATE_NO_WINDOW` | GUI 打包下 pg_ctl/initdb/psql 不再弹出控制台窗口（误关即杀数据库进程的根因） |
| 2026-09-06 | opc-central-dispatch-hub/launcher/desktop.py | 修 | `_apply_dark_titlebar` 改用 `FindWindowW` 按标题定位句柄（轮询放宽至 5 分钟覆盖首启耗时）；`_setup_logging` 幂等化（清 root handlers 重挂 FileHandler）并在迁移后重挂 | 深色标题栏可靠生效；修复 Alembic fileConfig 替换 root handlers 导致迁移后日志静默丢失 |
| 2026-09-06 | opc-central-dispatch-hub/scripts/build_desktop.py | 修 | `step_npm_build` 用 `shutil.which("npm")` 定位 npm.cmd | 修复 subprocess 直接调 "npm" 报 WinError 2（Windows 无 shell 不解析 .cmd） |
| 2026-09-17 | opc-central-dispatch-hub/assets/tilapia-icon.ico | 增 | - | 应用图标（多尺寸 16-256，源 ico/tilapia-icon.ico），供窗口/任务栏/exe 使用 |
| 2026-09-17 | opc-central-dispatch-hub/launcher/desktop.py | 修 | 新增 `_icon_path()`；`FolderPickerApi` 改名 `DesktopApi` 并新增 `minimize_window`/`toggle_maximize_window`（IsZoomed 判态）/`close_window`；`_open_window` 改 `frameless=True + maximized=True`（原 1280x860 窗口化）并 `webview.start(icon=...)`；`_open_fallback_window` 同步传 icon | 去除系统标题栏（页面独占窗口、最大化不覆盖任务栏）；修复窗口/任务栏显示 Python 默认图标（pywebview 未传 icon 时回退提取 sys.executable 图标）；frameless 后窗口控制由前端经 DesktopApi 调用 |
| 2026-09-17 | opc-central-dispatch-hub/launcher/tilapia-hub.spec | 修 | EXE 新增 `icon=` | 打包 exe 在资源管理器/任务栏显示 Tilapia 图标 |
| 2026-09-17 | opc-central-dispatch-hub/installer/tilapia-hub.iss | 修 | `[Setup]` 新增 `SetupIconFile` | 安装包向导 exe 使用 Tilapia 图标 |
| 2026-09-17 | opc-web-console/src/utils/desktopShell.ts | 修 | `PywebviewApi` 新增三个窗口控制方法类型；新增 `minimizeDesktopWindow`/`toggleMaximizeDesktopWindow`/`closeDesktopWindow` 封装（失败静默） | 前端类型声明与桌面壳窗口控制桥接 |
| 2026-09-17 | opc-web-console/src/hooks/useDesktopShell.ts | 增 | `useDesktopShell()`（初始检测 + `pywebviewready` 事件二次确认） | 解决 pywebview JS 桥异步注入导致的首帧漏判 |
| 2026-09-17 | opc-web-console/src/components/DesktopTitleBar.tsx + DesktopTitleBar.css | 增 | `DesktopTitleBar()`（pywebview-drag-region 拖动区 + 双击最大化 + 线条 SVG 三按钮） | frameless 无系统标题栏后的自绘标题栏：拖动/最小化/最大化还原/关闭；浏览器环境不渲染 |
| 2026-09-17 | opc-web-console/src/components/AppLayout.tsx | 修 | 外层包 `div.app-shell`（桌面模式加 `--desktop` 修饰类）；挂载 `DesktopTitleBar` | 自绘标题栏挂载点与桌面模式样式开关 |
| 2026-09-17 | opc-web-console/src/components/AppLayout.css | 修 | 新增 `.app-shell--desktop` 4 条覆盖（sider top/height、main padding-top、header sticky top、content min-height） | 桌面模式布局整体下移 32px 为自绘标题栏让位，避免内容溢出 |
| 2026-09-17 | opc-central-dispatch-hub/launcher/desktop.py | 修 | `_apply_dark_titlebar` 重写为 `_tune_window_chrome()`：仅清除 WS_CAPTION 保留 WS_THICKFRAME（边缘原生拖拽缩放 + Aero Snap），加 WS_EX_APPWINDOW（无边框窗口默认不进任务栏），SW_RESTORE + 延时 + `_fill_workarea` 修复 WebView2 子窗口隐藏与还原布局竞态 | 去标题栏后页面独占窗口、边缘可自由缩放、任务栏正常显示图标、不覆盖任务栏；顺带修复 wait_ready 误判端口占用者导致空白页（见 launcher/server.py） |
| 2026-09-17 | opc-central-dispatch-hub/launcher/desktop.py | 修 | 新增 `_MONITORINFO`/`_workarea_of`/`_fill_workarea`；`toggle_maximize_window` 改为按"是否铺满工作区"判断（无边框窗口系统二次最大化会覆盖任务栏，手动铺满替代） | 双击顶部区域最大化/还原不覆盖任务栏 |
| 2026-09-17 | opc-central-dispatch-hub/launcher/server.py | 修 | `wait_ready` 弃用 HTTP /docs 探测，改判 uvicorn `server.started` + 服务线程存活快速失败；移除 urllib import | 修复端口被其他进程占用时探测打到占用者服务误判就绪（窗口空白页）；bind 失败秒级报错替代 30s 超时 |
| 2026-09-17 | opc-central-dispatch-hub/launcher/desktop.py | 修 | `create_window` 新增 `background_color=#1a1b1e` | 加载期深色底，避免白屏闪烁 |
| 2026-09-17 | opc-web-console/src/utils/desktopShell.ts | 修 | PywebviewApi 收敛为 begin_move_window/toggle_maximize_window；封装改 `beginMoveDesktopWindow`/`toggleMaximizeDesktopWindow`（删 minimize/close 封装） | 自绘标题栏方案废弃，窗口控制收敛为拖动 + 双击最大化 |
| 2026-09-17 | opc-web-console/src/components/DesktopTitleBar.tsx + DesktopTitleBar.css、src/hooks/useDesktopShell.ts | 减 | 删除文件 | 用户不要独立标题条；拖动/双击最大化改挂在页面 Header 上 |
| 2026-09-17 | opc-web-console/src/components/AppLayout.tsx | 修 | 恢复原 Layout 结构；Header 新增 onMouseDown（桌面壳左键进入系统拖动，closest('button, a, input, .ant-avatar') 排除交互元素）与 onDoubleClick（切换最大化） | 无标题栏后的拖动与最大化入口，浏览器环境不触发 |
| 2026-09-17 | opc-web-console/src/components/AppLayout.css | 减 | 删除 `.app-shell--desktop` 覆盖规则 | 独立标题栏方案废弃，页面直接从窗口顶部开始 |
| 2026-09-17 | opc-central-dispatch-hub/launcher/desktop.py | 修 | `begin_move_window` 重写为循环跟随方案（GetCursorPos + SetWindowPos 每帧定位至"光标-偏移"，GetAsyncKeyState 检测左键释放；拖动铺满态窗口先 restore）；恢复 `minimize_window`/`close_window`；顶部 `import time` | 修复无法拖动（WebView2 持有鼠标捕获致 HTCAPTION 方案失效）；拖最大化窗口自动还原；右上角窗口控制按钮的后端支撑 |
| 2026-09-17 | opc-web-console/src/components/WindowControls.tsx + WindowControls.css | 增 | `WindowControls()`（右上角最小化/最大化还原/关闭三按钮，线条 SVG，关闭 hover 红；非桌面壳不渲染） | 用户要求参考 VS Code 在窗口右上角提供窗口控制按钮 |
| 2026-09-17 | opc-web-console/src/hooks/useDesktopShell.ts | 增 | 重建 `useDesktopShell()` | 桥异步注入的桌面模式检测（WindowControls 渲染开关） |
| 2026-09-17 | opc-web-console/src/utils/desktopShell.ts | 修 | PywebviewApi 补 `minimize_window`/`close_window`；新增 `minimizeDesktopWindow`/`closeDesktopWindow` 封装 | 窗口控制按钮的前端桥接 |
| 2026-09-17 | opc-web-console/src/components/AppLayout.tsx | 修 | Header 右侧 Space 内 Avatar 后挂载 `WindowControls` | 按钮位于窗口右上角；Header 拖动 mousedown 已排除 button 目标不误触 |

（开发过程中持续追加，格式同上）
