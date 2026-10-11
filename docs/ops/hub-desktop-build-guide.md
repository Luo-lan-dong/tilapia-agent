# 中枢 Windows 桌面版构建发布指南

关联 ADR：[ADR-021](../adr/021-hub-windows-desktop-deployment.md)
适用范围：把中枢（opc-central-dispatch-hub）编译为 Windows 桌面 exe / 安装程序。

## 1. 两种运行形态

| 形态 | 数据库 | 前端 | 启动方式 |
|------|--------|------|---------|
| 开发形态 | `.env.local` 指向的库（远程/本地） | Vite dev server（5173，代理 /api） | `uvicorn main:app --reload` + `npm run dev` |
| 桌面形态 | 内嵌 PostgreSQL（`127.0.0.1:15432`，数据在 `data/pg/`） | build 产物由 FastAPI 托管（8000 端口） | 双击 exe → 桌面窗口（pywebview/WebView2） |

两形态共用同一套代码，桌面模式全部配置由 launcher 注入环境变量，不读 `.env.local`。

## 2. 环境准备（一次性）

```
1. Python 3.11+，创建并激活 venv：
   cd opc-central-dispatch-hub
   python -m venv .venv
   .venv\Scripts\activate
   pip install -e ".[dev,desktop]"     # desktop 组含 pywebview 与 pyinstaller

2. Node.js 18+（前端构建用）

3. PostgreSQL 便携版（内嵌数据库，打包与本地验证必需）：
   .venv\Scripts\python.exe scripts\prepare_desktop_bundle.py
   # 自动下载 EDB postgresql-16.9 windows-x64 binaries 并解压到 pgsql/
   # 国内网络慢时可先手动下载 zip，再执行 --zip <本地zip路径>
   # 产物约 315MB 下载 / 1.2GB 解压
```

## 3. 桌面模式源码运行（不打包，日常调试用）

```
cd opc-central-dispatch-hub
.venv\Scripts\python.exe -m launcher
```

- 首次启动自动完成：initdb → 建库（opc/opc，随机密码）→ alembic 迁移 → 初始版本数据
- 弹出桌面窗口「OPC 中枢调度台」；关闭窗口即停止全部服务
- 日志：`data/logs/launcher.log`（launcher 阶段）与应用日志
- 无窗口验证（CI/无人值守）：`.venv\Scripts\python.exe scripts\verify_desktop_launcher.py`
- 数据重置：停止程序后删除 `data/` 目录即可（下次启动重新初始化）

## 4. 编译 exe（推荐：一键构建）

### 一键构建（日常使用）

```
cd opc-central-dispatch-hub
.venv\Scripts\python.exe scripts\build_desktop.py
```

脚本自动完成：停止运行中的实例 → 检查 pgsql → 复制前端产物 → PyInstaller → 把 pgsql 链接进产物。

可选参数：
- `--npm-build`：前端代码有改动时，打包前先执行 `npm run build`（默认复用现有 web/ 产物）
- `--copy-pgsql`：把 pgsql 真实复制进产物（默认用目录链接，秒级完成）

构建完成后 `dist\tilapia-hub\tilapia-hub.exe` 即可直接双击使用。

### 手动分步构建（理解原理用）

在仓库根目录的 `opc-central-dispatch-hub` 下依次执行：

### 步骤 1：构建前端产物

```
cd ..\opc-web-console
npm install        # 首次或依赖变更时
npm run build      # 产出 dist/
cd ..\opc-central-dispatch-hub
```

### 步骤 2：复制前端产物到中枢 web/ 目录

```
.venv\Scripts\python.exe scripts\copy_web_dist.py
# dist/ → web/；web/ 缺失时打包出的 exe 为纯 API 后端（无界面）
```

### 步骤 3：准备 PostgreSQL 便携版

```
.venv\Scripts\python.exe scripts\prepare_desktop_bundle.py
# 已存在时跳过；产物 pgsql/ 打包时会进入安装包（由 Inno Setup 复制）
```

### 步骤 4：PyInstaller 打包

```
.venv\Scripts\python.exe -m PyInstaller launcher\tilapia-hub.spec --noconfirm --distpath dist --workpath build\pyinstaller
```

产物：`dist\tilapia-hub\`（onedir 目录，含 `tilapia-hub.exe`、`_internal\`、`web\`、`alembic\`、`static\`）

要点说明（spec 已固化，勿随意改动）：
- 入口为 `launcher\__main__.py`（**必须绝对路径**，spec 相对路径以 spec 目录解析）
- datas 随包：`alembic\`、`alembic.ini`、`static\`、`assets\`、`web\`
- hiddenimports：uvicorn 各子模块、alembic、app.models.*（Alembic env.py 运行时依赖）

### 步骤 5：本地验证 exe

```
# 模拟安装器布局：把 pgsql 链接进产物目录（正式安装包由 Inno Setup 复制）
New-Item -ItemType Junction -Path "dist\tilapia-hub\pgsql" -Target "<绝对路径>\opc-central-dispatch-hub\pgsql"

dist\tilapia-hub\tilapia-hub.exe
# 预期：桌面窗口弹出；http://127.0.0.1:8000 返回前端页面；
# /api/v1/settings/versions 返回 v0.1.0；关窗后 postgres 进程全部退出
```

### 步骤 6：制作安装程序（Inno Setup 6）

```
1. 安装 Inno Setup 6（https://jrsoftware.org/isinfo.php）
2. 编译：
   iscc installer\tilapia-hub.iss
   # 或用 Inno Setup Compiler GUI 打开 installer\tilapia-hub.iss 编译
3. 产物：installer\Output\TilapiaHub-Setup-x64.exe
```

安装包行为：
- 按用户级安装到 `%LOCALAPPDATA%\Programs\TilapiaHub`（无需管理员权限）
- 自动复制 `pgsql\`（内嵌数据库）与主程序；创建开始菜单/桌面快捷方式
- 卸载时一并删除 `data\`（内嵌数据库数据），向导中已明示

## 5. 发版前测试清单（对应 harness E2E）

| 编号 | 测试 | 通过标准 |
|------|------|---------|
| E2E-1 | 干净 Win10/11 虚拟机安装→启动 | 无需 Python/PG，窗口直接可用，更新日志显示版本 |
| E2E-2 | 数据持久化 | 建任务→关窗→重开，数据仍在 |
| E2E-3 | 单实例 + 端口占用 | 二次启动提示已运行；8000 被占自动换 8001+ |
| E2E-4 | 卸载 | 安装目录与快捷方式移除，无残留服务 |

自动化回归（打包前必须全绿）：

```
.venv\Scripts\python.exe -m pytest tests -q        # 204 项
.venv\Scripts\python.exe -m ruff check app launcher tests
.venv\Scripts\python.exe -m ruff format --check app launcher tests/launcher
```

## 6. 远程数据导入（可选，数据迁移/备份恢复）

远程库（PG 18）的 pg_dump 文件可导入本地内嵌库（PG 16）：

```
.venv\Scripts\python.exe scripts\import_remote_dump.py ..\sql\<dump文件>.sql
```

- 自动过滤 PG 18 专属内容（`\restrict` / `\unrestrict` 元命令、`SET transaction_timeout`）
- 导入为覆盖式（dump 自带 DROP/CREATE），导入后打印各表行数核对
- 前提：内嵌 PG 已启动（先跑一次 `python -m launcher` 或 `verify_desktop_launcher.py`）

## 7. 常见问题

| 现象 | 原因/处理 |
|------|----------|
| 打包时报 "另一个程序正在使用此文件 ... launcher.log" | 上一次的 exe 实例还在运行；一键脚本已自动处理，手动打包前先关掉 tilapia-hub.exe 和 postgres.exe |
| exe 弹窗「未找到内嵌 PostgreSQL 组件」 | 产物目录缺 `pgsql\`（PyInstaller 打包会清空 dist）；用一键构建脚本自动补齐，或手动 `mklink /J dist\tilapia-hub\pgsql ..\..\pgsql` |
| 启动卡住，日志停在 `desktop_startup_begin` | pg_ctl 管道句柄被 postmaster 继承；确认使用修复后的 `pg_manager.py`（输出重定向到文件） |
| lifespan 报 "Event loop is closed" | 确认 `db_setup.seed_initial_data` 结束时执行了 `engine.dispose()` |
| uvicorn 线程启动即退、wait_ready 30s 超时 | frozen 下不能用 "main:app" 字符串导入；确认 `server.start()` 延迟 `create_app()` 传入实例 |
| 错误弹窗但日志无 ERROR 记录 | Alembic fileConfig 禁用了既有 logger；确认 `env.py` 使用 `disable_existing_loggers=False` |
| 8000-8010 全被占用 | 关闭占用进程，或修改 `launcher/port.py` 的端口范围 |
| 双击 exe 窗口白屏 | 缺 WebView2 Runtime，安装 https://developer.microsoft.com/microsoft-edge/webview2/ |
| 双击提示「中枢已在运行」 | 单实例锁正常工作；先关闭已有窗口再启动 |
| 深色标题栏未生效 | 确认 launcher.log（或 app.log）有 `desktop_dark_titlebar_applied`；Win10 1809 以下不支持 DWM 深色标题栏属性 |
| 构建时报 WinError 2 找不到 npm | 已修复（`shutil.which("npm")` 定位 npm.cmd）；确认 Node.js 在 PATH |
| 控制台刷 `--- Logging error --- cannot unpack non-iterable NoneType` | 已修复（脱敏 filter 不再置空 args）；确认 `app/core/logging.py` 为修复后版本 |
| 源码模式与桌面版互踢（InvalidPasswordError） | 已修复（PG 端口自动避让：被其他实例占用时自动顺延 15433+） |
