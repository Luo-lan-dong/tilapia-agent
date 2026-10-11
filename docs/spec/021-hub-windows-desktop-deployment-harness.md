# 中枢 Windows 桌面一体化部署 Harness

关联 ADR：[ADR-021](../adr/021-hub-windows-desktop-deployment.md)
关联 Spec：[021-hub-windows-desktop-deployment-spec.md](./021-hub-windows-desktop-deployment-spec.md)

## 1. 测试金字塔

```
        E2E（手动/半自动）
       ┌─────────────────┐
       │ 安装→启动→窗口→关闭 │  1 个核心场景（干净机器）
       └─────────────────┘
      集成测试（pytest 可自动）
     ┌───────────────────────┐
     │ 静态托管/SPA fallback/   │
     │ launcher 单元化流程      │
     └───────────────────────┘
   单元测试（pytest 可自动）
  ┌─────────────────────────────┐
  │ 端口探测/单实例锁/路径定位/     │
  │ pg_env 读写/密码生成          │
  └─────────────────────────────┘
```

## 2. 单元测试清单（可自动化，CI 门禁）

| 编号 | 测试项 | 文件 | 断言要点 |
|------|--------|------|---------|
| UT-1 | 端口探测：首个空闲端口 | `tests/launcher/test_port.py` | 8000 空闲返回 8000；8000 被占返回 8001；全占用抛 `PortExhaustedError` |
| UT-2 | 端口探测范围边界 | `tests/launcher/test_port.py` | 探测范围为 8000-8010，不越界 |
| UT-3 | 密码生成强度 | `tests/launcher/test_pg_manager.py` | 长度 ≥24、包含大小写数字、两次生成不同 |
| UT-4 | pg_env.json 读写往返 | `tests/launcher/test_pg_manager.py` | 写入后读出字段一致；损坏 JSON 返回 None 并重建 |
| UT-5 | 安装目录路径定位（frozen） | `tests/launcher/test_paths.py` | `sys.frozen` 时以 exe 所在目录为根；源码运行时以仓库目录为根 |
| UT-6 | 单实例锁互斥 | `tests/launcher/test_instance_lock.py` | 二次 acquire 返回 False；release 后可重新 acquire |

## 3. 集成测试清单（可自动化，CI 门禁）

| 编号 | 测试项 | 文件 | 断言要点 |
|------|--------|------|---------|
| IT-1 | 静态托管：根路径返回 index.html | `tests/api/test_static_spa.py` | `GET /` 200 且 Content-Type 为 text/html |
| IT-2 | SPA fallback：history 路由刷新 | `tests/api/test_static_spa.py` | `GET /tasks`、`/settings/llm` 200 返回 index.html |
| IT-3 | API 优先级不受影响 | `tests/api/test_static_spa.py` | `GET /api/v1/agent-nodes` 等仍走 API；未知 `/api/xxx` 返回 404 JSON 而非 index.html |
| IT-4 | 静态资源命中 | `tests/api/test_static_spa.py` | `GET /assets/<构建产物文件>` 200 |
| IT-5 | web/ 缺失时不挂载 | `tests/api/test_static_spa.py` | 无 web/ 目录时 `GET /` 404，API 正常 |
| IT-6 | launcher 端到端干跑（mock PG） | `tests/launcher/test_startup_flow.py` | mock pg_ctl/initdb 后按序调用；任一步失败按 Spec §4 降级退出 |

## 4. E2E 测试清单（手动，发版前执行）

| 编号 | 场景 | 步骤 | 预期 |
|------|------|------|------|
| E2E-1 | 干净机器安装启动全链路 | 1) 干净 Win10/11 虚拟机（无 Python/PG）2) 运行 TilapiaHub 安装程序 3) 双击桌面快捷方式 | 安装完成；桌面窗口展示控制台；侧边栏显示版本；`data/pg/` 生成；更新日志页显示 v0.1.0 |
| E2E-2 | 数据持久化 | 在 E2E-1 基础上创建任务 → 关闭窗口 → 重新双击 | 任务仍存在（数据落本地 PG） |
| E2E-3 | 单实例与端口占用 | 运行中再次双击；另起进程占用 8000 后启动 | 二次启动提示已运行；8000 被占时窗口自动换端口打开 |
| E2E-4 | 卸载 | 控制面板卸载 TilapiaHub | 安装目录与快捷方式移除；无残留系统服务 |

## 5. CI 门禁

| 门禁 | 要求 |
|------|------|
| lint | `ruff format --check` + `ruff check`（含 launcher/） |
| test | 全部现有测试 + UT-1~UT-6、IT-1~IT-6 通过 |
| 文件长度 | `scripts/check_file_size.py`：launcher 各文件 ≤300 行 |
| 构建（发版） | PyInstaller 打包成功且产物可启动；Inno Setup 编译安装包成功 |
| E2E（发版） | E2E-1~E2E-4 在干净虚拟机执行通过后方可发版 |
