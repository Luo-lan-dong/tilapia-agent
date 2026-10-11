# ADR-021：中枢 Windows 桌面一体化部署（内嵌 PostgreSQL + 桌面窗口）

## 状态

Accepted

## 日期

2026-09-05

## 问题背景

当前中枢（opc-central-dispatch-hub）的部署形态面向开发者/服务器场景：

1. **依赖外部数据库**：`.env.local` 指向局域网服务器 `192.168.101.101` 的 PostgreSQL/Redis/MinIO，脱离该服务器无法运行。
2. **部署门槛高**：需要手工配置 `.env`、命令行启动后端、另起前端 dev server（Vite 5173），对非开发者不可用。
3. **代码事实核查**（2026-09-05）：Redis 与 MinIO 在应用代码中**仅有配置项、零调用**（调度/心跳走数据库 + 内存循环；文件存储未实现，静态资源走本地 `static/` 目录）。中枢真正的硬依赖只有 PostgreSQL。

### 需求（用户已确认）

1. 数据库本地化：不再依赖远程数据库，数据存在本机。
2. 产品形态：像普通 Windows 软件一样安装进系统（安装向导），双击启动。
3. 双击后同时启动后端与前端，前端以**独立桌面窗口**呈现（不再是浏览器输地址访问）。

## 决策

### 1. 内嵌 PostgreSQL 便携版（数据库本地化）

- 安装包内附带 PostgreSQL Windows x64 **zip 二进制便携版**（解压即用，不注册系统服务、不写注册表）。
- 首次启动流程：`initdb` 初始化数据目录 → `pg_ctl start` 启动（仅监听 `127.0.0.1`）→ 建库 → `alembic upgrade head` → 执行 ADR-018 初始版本数据脚本（幂等）。
- 端口使用 **15432**（非默认 5432），避免与本机已有 PostgreSQL 冲突。
- 数据目录位于安装目录 `data/pg/`，卸载/重装互不影响（数据随安装目录存在）。
- 应用侧通过环境变量注入 `DATABASE_URL=postgresql+asyncpg://opc:<随机生成密码>@127.0.0.1:15432/opc`，不写 `.env` 文件。

### 2. 前端静态化：FastAPI 托管前端产物（单端口）

- 前端 `npm run build` 产物（`opc-web-console/dist`）复制到中枢 `web/` 目录，由 FastAPI `StaticFiles` 托管。
- API 路由（`/api/*`、`/ws/*` 等）优先匹配，其余路径回退到 `index.html`（SPA history 路由 fallback）。
- 桌面模式下前端与后端共用 **8000 端口**（占用时自动向后探测 8001-8010），前端 dev server（Vite）仅开发期使用。

### 3. pywebview 桌面窗口壳

- 使用 pywebview 创建桌面窗口加载 `http://127.0.0.1:<port>`，渲染引擎为 Windows 自带 WebView2。
- 关闭窗口 = 停止 uvicorn + `pg_ctl stop` 停止内嵌 PG，进程退出。
- 单实例锁：启动时探测端口/互斥锁，已有实例运行则激活提示并退出新进程，避免端口冲突与双份数据目录。

### 4. 打包与安装

- **PyInstaller（onedir 模式）** 打包后端 + launcher；`alembic/` 目录、`web/` 静态产物、`pgsql/` 便携版作为数据文件随包分发。
- **Inno Setup** 制作安装向导（`PrivilegesRequired=lowest`，默认安装到 `%LOCALAPPDATA%\Programs\TilapiaHub`，避免 UAC 与运行期写入权限问题），生成开始菜单/桌面快捷方式。

## 备选方案

### 数据库本地化

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| A：内嵌 PostgreSQL 便携版 | 开箱即用；与现有 SQLAlchemy/Alembic/PG 特性完全兼容 | 包体积 +300MB；需管理 pg 生命周期 | ✅ 选中（用户已确认） |
| B：用户自装 PostgreSQL | 实现最简单 | 用户需自行安装配置数据库，体验差 | ❌ 否决（用户已确认） |
| C：换 SQLite | 最轻量、单文件 | JSONB、partial unique index、`FOR UPDATE SKIP LOCKED`（ADR-015/016）等 PG 特性需逐一改造，违反 ADR-003 | ❌ 否决（用户已确认） |

### 桌面窗口壳

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| A：pywebview | Python 同栈、WebView2 系统自带、与 PyInstaller 打包链路一致、包体增量小 | 界面控制力受 WebView2 限制 | ✅ 选中（用户已确认） |
| B：Electron | 界面控制力强 | 引入 Node 运行时，+150MB，与 Python 后端两套进程体系 | ❌ 否决（用户已确认） |
| C：Tauri | 包体小、性能好 | 需引入 Rust 工具链，栈不匹配 | ❌ 否决（用户已确认） |

### 前端服务形态

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| A：FastAPI StaticFiles 托管 build 产物 | 单端口、零额外进程、架构不变 | 每次发版需先 build 前端 | ✅ 选中 |
| B：打包内置 Node dev server | 无需改造托管层 | 依赖 Node 环境、dev server 不适合生产 | ❌ 否决 |

## 决策原因

1. **代码事实支撑**：Redis/MinIO 零调用，本地化改造面收敛为「PG 一个依赖」，内嵌便携版是性价比最高的开箱即用方案。
2. **不破坏现有开发流程**：`.env.local` 开发路径保留；desktop 模式通过环境变量注入配置（优先级高于 env_file），同一套代码两种运行形态。
3. **pywebview 与后端同栈**：生命周期管理（窗口关闭 → 服务与 PG 退出）在同一进程内可控，避免跨进程编排。
4. **端口避让策略**：PG 用 15432、HTTP 自动探测 8000-8010，降低与用户本机已有软件的冲突概率。

## 影响

### 对系统架构的影响

- 中枢新增「desktop 运行形态」：`launcher/` 模块承担单实例锁、端口探测、PG 生命周期、迁移执行、uvicorn 启动、窗口创建。
- FastAPI 新增前端静态托管与 SPA fallback（对 API 路由零影响，API 路由优先匹配）。
- 数据库仍是 PostgreSQL，无表结构变更；Alembic 迁移机制不变，只是执行时机变为首次启动自动执行。

### 对开发的影响

- 新增 `opc-central-dispatch-hub/launcher/`（多文件，单文件 ≤300 行）：`__main__.py` / `paths.py` / `instance_lock.py` / `port.py` / `pg_manager.py` / `db_setup.py` / `server.py` / `desktop.py`。
- `app/main.py` 追加静态托管挂载逻辑（`web/` 目录存在时生效）。
- `opc-web-console` 构建产物复制脚本 `scripts/copy_web_dist.py`。
- 新增打包配置 `launcher/tilapia-hub.spec`（PyInstaller）与 `installer/tilapia-hub.iss`（Inno Setup）。
- `config.py` 的 `redis_url` 等必填字段在 desktop 模式由 launcher 注入占位值，不改字段定义。

### 对运维的影响

- 安装即用：安装向导 → 双击快捷方式 → 桌面窗口直接可用，无需命令行。
- 数据备份 = 拷贝安装目录 `data/pg/`；卸载不影响 `Program Files` 外的系统组件（未注册服务、未写注册表）。
- Agent 节点（Linux）通过 HTTP/WS 连中枢的接入方式不变；`HUB_PUBLIC_URL` 改为安装目录下配置文件提供。

### 对安全的影响

- 内嵌 PG 仅监听 `127.0.0.1`，密码首次启动随机生成并落盘 `data/pg_env.json`（仅本机用户可读）。
- 桌面模式默认绑定 `127.0.0.1`；若需局域网 Agent 接入，通过配置显式开启 `0.0.0.0`（沿用 `--host 0.0.0.0` 约定）。
- 不引入新的鉴权面，沿用现有 `AGENT_REGISTRATION_TOKEN` 机制。

## 关联文档

- [ADR-003 数据库与缓存选型](./003-database-stack.md)
- [ADR-018 中枢系统版本管理与更新日志](./018-system-version-management.md)（初始数据脚本复用）
- [deployment-architecture.md](../architecture/deployment-architecture.md)（服务器形态部署，与本 ADR 并存）
- [021-hub-windows-desktop-deployment-spec.md](../spec/021-hub-windows-desktop-deployment-spec.md)
- [021-hub-windows-desktop-deployment-harness.md](../spec/021-hub-windows-desktop-deployment-harness.md)
- [021-hub-windows-desktop-deployment-changes.md](../spec/021-hub-windows-desktop-deployment-changes.md)

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-09-05 | 初始创建：内嵌 PostgreSQL 便携版 + FastAPI 托管前端静态产物 + pywebview 桌面窗口 + PyInstaller/Inno Setup 打包 | AI助手 |
