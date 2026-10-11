# 中枢 Windows 桌面一体化部署 Spec

关联 ADR：[ADR-021](../adr/021-hub-windows-desktop-deployment.md)
关联 Harness：[021-hub-windows-desktop-deployment-harness.md](./021-hub-windows-desktop-deployment-harness.md)
关联 Changes：[021-hub-windows-desktop-deployment-changes.md](./021-hub-windows-desktop-deployment-changes.md)

## 1. 功能描述

为中枢提供「Windows 桌面软件」形态的部署与运行能力：

1. 安装程序将中枢安装进系统（快捷方式），开箱即用。
2. 双击启动后自动拉起：内嵌 PostgreSQL → 数据库迁移/初始化 → FastAPI 后端（托管前端静态产物）→ 桌面窗口（pywebview）。
3. 数据全部保存在本机安装目录，不依赖远程数据库。
4. 关闭窗口即停止全部服务。

开发形态（命令行 + `.env.local` + 远程/本地 PG + Vite dev server）完全保留，两形态共存。

## 2. 用户故事

| 编号 | 用户故事 | 验收要点 |
|------|---------|---------|
| US-1 | 作为用户，我运行安装程序，希望像装普通软件一样把中枢装进系统 | Inno Setup 向导完成安装，生成开始菜单/桌面快捷方式 |
| US-2 | 作为用户，我双击快捷方式，希望直接看到可用的管理控制台 | 桌面窗口打开并展示前端页面；后端与内嵌 PG 已自动启动 |
| US-3 | 作为用户，我希望数据保存在自己电脑上，不连远程数据库 | PG 数据目录位于安装目录 `data/pg/`；`DATABASE_URL` 指向 `127.0.0.1:15432` |
| US-4 | 作为用户，我关闭窗口后希望程序完全退出 | uvicorn 与 postgres 进程均退出，无残留 |
| US-5 | 作为用户，重复双击图标时我不希望出现两个实例或报错 | 单实例锁生效，二次启动提示后退出 |
| US-6 | 作为开发者，我希望原有开发流程不受影响 | `.env.local` + 命令行启动路径行为不变；测试全部通过 |

## 3. 核心流程

### 3.1 桌面模式启动流程

```
用户双击 tilapia-hub.exe
        │
        ▼
[launcher] 单实例检查
   ├─ 已有实例运行 ──► 提示「已在运行」并退出
   │
   ▼ 无实例
[launcher] 目录准备（data/、logs/）
        │
        ▼
[launcher] 端口探测 8000→8010，取第一个空闲端口 P
        │
        ▼
[launcher] 内嵌 PG 生命周期
   ├─ data/pg/ 不存在 ──► initdb（encoding=UTF8，仅本机监听）
   │                        → 生成随机密码 → 写 data/pg_env.json
   ├─ pg_ctl start（-o "-p 15432 -c listen_addresses=127.0.0.1"）
   │     └─ 等待就绪（pg_isready 轮询，超时 60s）
   └─ 建库/建用户（幂等：存在则跳过）
        │
        ▼
[launcher] 注入环境变量
   DATABASE_URL=postgresql+asyncpg://opc:***@127.0.0.1:15432/opc
   REDIS_URL=redis://127.0.0.1:15439/0 （占位，代码零调用）
   APP_ENV=desktop  DEBUG=false  PORT=P ...
        │
        ▼
[launcher] 数据库迁移
   alembic upgrade head（幂等）
   → scripts/insert_initial_version.py 逻辑（幂等）
        │
        ▼
[launcher] 后台线程启动 uvicorn(app, port=P, host=127.0.0.1)
        │
        ▼
[launcher] 轮询 http://127.0.0.1:P/health 就绪（超时 30s）
        │
        ▼
[pywebview] 创建窗口（约 1280x860）加载 http://127.0.0.1:P
        │
        ▼
窗口关闭事件 ──► 停止 uvicorn ──► pg_ctl stop ──► 进程退出
```

### 3.2 前端静态托管（新增，两形态共用）

```
请求 ──► FastAPI
   ├─ /api/*、/ws/* 等已注册路由 ──► API 处理（优先级最高，行为不变）
   ├─ /assets/*、静态文件命中 web/ ──► StaticFiles 返回
   └─ 其余 GET 路径 ──► 回退 web/index.html（SPA history 路由 fallback）
                        web/ 目录不存在（纯后端部署）──► 不挂载，404 行为与现状一致
```

### 3.3 开发形态（保持不变）

```
.env.local → uvicorn main:app --reload（后端 8000）
opc-web-console → npm run dev（Vite 5173，代理 /api）
```

## 4. 降级链路

| 场景 | 行为 |
|------|------|
| 8000-8010 全部被占用 | 窗口内展示错误信息「端口不足」，日志记录 `desktop_port_exhausted`，进程退出 |
| `initdb` 失败（磁盘/权限） | 窗口内展示错误信息，日志记录 `desktop_pg_init_failed`，进程退出 |
| `pg_ctl start` 超时 | 重试 1 次；仍失败则窗口展示错误，日志 `desktop_pg_start_failed`，退出 |
| alembic 迁移失败 | 窗口展示错误，日志 `desktop_migrate_failed`，退出（不启动半可用服务） |
| web/ 静态产物缺失 | 后端 API 照常可用；访问根路径 404（纯后端形态） |
| 二次启动 | 提示「中枢已在运行」，不产生第二个实例 |
| WebView2 运行时缺失 | pywebview 抛出明确异常，提示安装 WebView2 Runtime，日志 `desktop_webview_missing` |

## 5. 目录与配置约定

```
%LOCALAPPDATA%\Programs\TilapiaHub\     ← Inno Setup 默认安装目录
├── tilapia-hub.exe                     ← PyInstaller onedir 主程序
├── _internal/                          ← PyInstaller 依赖
├── web/                                ← 前端 build 产物
├── pgsql/                              ← PostgreSQL 便携版（bin、lib、share）
├── alembic/ + alembic.ini              ← 迁移脚本（首次启动执行）
├── data/
│   ├── pg/                             ← PG 数据目录（initdb 产物，含全部业务数据）
│   └── pg_env.json                     ← 内嵌 PG 连接信息（随机密码，仅本机用户可读）
└── logs/                               ← 运行日志
```

- 桌面模式不读取 `.env.local`，全部配置由 launcher 注入；`data/pg_env.json` 记录 `port`、`password` 等持久化参数。
- 卸载程序仅删除安装目录（用户数据随之删除，安装向导中明示）。

## 6. 验收标准

1. 在干净 Windows 10/11（无 Python、无 PostgreSQL）上：运行安装程序 → 完成 → 双击快捷方式 → 桌面窗口展示控制台首页，无需任何命令行操作。
2. `data/pg/` 存在且 `pg_env.json` 中 `DATABASE_URL` 指向 `127.0.0.1:15432`；任务、节点等数据均落在该实例。
3. 浏览器访问 `http://127.0.0.1:8000/tasks`、`/settings/llm` 等 history 路由刷新后正常（SPA fallback 生效）；`/api/v1/*` 行为与现状一致。
4. 关闭窗口后 `postgres.exe`、`tilapia-hub.exe` 进程均退出。
5. 程序运行中再次双击图标：新进程提示已运行并退出，原实例不受影响。
6. 8000 端口被占用时自动使用 8001+，窗口正常打开。
7. 开发形态回归：`pytest` 全部通过；`uvicorn main:app` + Vite dev server 的原有流程不受影响。
8. Alembic 迁移与 ADR-018 初始版本数据在首次启动自动完成，更新日志页显示 `v0.1.0`。
