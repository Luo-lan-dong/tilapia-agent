# tilacode 品牌化路径重命名 Spec

> 关联 ADR：[ADR-019](../adr/019-tilacode-branding-rename.md) · 变更清单：[019-tilacode-branding-changes.md](./019-tilacode-branding-changes.md)

## 功能描述

将 tilacode（opencode 二次开发项目）对外可见的路径体系从 opencode 品牌全量切换为 tilacode 品牌：工作目录配置目录 `.opencode/` → `.tilacode/`、配置文件 `opencode.json(c)` → `tilacode.json(c)`、环境变量 `OPENCODE_*` → `TILACODE_*`、全局数据目录 `opencode` → `tilacode`。硬切不兼容，Python 桥接层与 tilacode server 的运行时协议标识同步更名。

## 用户故事

- 作为**产品负责人**，我希望终端用户的项目工作目录中出现的是 `.tilacode/` 而非 `.opencode/`，使产品品牌统一
- 作为**运维人员**，我希望环境变量前缀统一为 `TILACODE_*`，部署配置不再出现 opencode 字样
- 作为**开发人员**，我希望新旧命名不做兼容分支，代码逻辑保持与上游一致的简洁度

## 核心流程

### 1. 配置发现流程（改名后）

```
tilacode 启动
  ├─ 读取全局配置：~/.config/tilacode/tilacode.json(c)          [global.ts: app="tilacode"]
  ├─ 向上搜索工作目录配置文件：tilacode.json / tilacode.jsonc    [core/config.ts: names]
  ├─ 向上搜索 .tilacode/ 目录（含 ~ 下的 .tilacode/）            [core/config.ts + config/paths.ts]
  ├─ TILACODE_CONFIG_DIR 环境变量指定的目录                       [flag.ts]
  └─ 合并顺序：全局 → 项目文件 → .tilacode 目录（近处优先）
```

### 2. Python 桥接层任务执行流程（改名后）

```
任务派发
  ├─ workspace.prepare：创建 {root}/{task_id}/.tilacode/
  ├─ 写入 .tilacode/tilacode.jsonc（权限 "*": "allow"、模型 opencode/mimo-v2.5-free）
  ├─ tilacode_manager 启动 server 子进程
  │    ├─ 注入 TILACODE_SERVER_PASSWORD
  │    └─ 解析 stdout "tilacode server listening on http://host:port"
  ├─ tilacode_client 请求携带 x-tilacode-directory 头（Basic Auth 用户名 tilacode）
  ├─ plan 模式：prompt 指定产物写入 .tilacode/plans/
  └─ 任务完成：读取 {ws}/.tilacode/plans/*.md 上报中枢
```

### 3. 降级链路

无新增降级链路。协议标识（listening 行 / directory 头 / 密码 env）为两端强耦合约定，**不存在降级**——任何一端未同步即整体失败，这是有意的硬切设计。

## 明确不改清单（与 ADR-019 §2 一致）

- `@opencode-ai/*` npm 包名、workspace 依赖
- `models.opencode.ai` / `console.opencode.ai` / `opencode.ai` 云端 URL
- provider ID `"opencode"`（模型 `opencode/mimo-v2.5-free` 的路由前缀）
- `$schema: https://opencode.ai/config.json`
- LLM 网关请求头（`x-opencode-session` / `x-opencode-request` / `x-opencode-client` / `x-opencode-project`）
- 内部 UI 头（`x-opencode-ticket` / `x-opencode-sync` / `x-opencode-title`）
- tilacode 根目录 `install` 脚本的 GitHub 下载 URL 与产物文件名（不在部署链路）

## 变更文件清单（源码级，完整函数级明细见 Changes）

### tilacode TS 源码（L1/L2）

| 文件 | 变更点 |
|------|--------|
| `packages/core/src/global.ts` | `app = "opencode"` → `"tilacode"` |
| `packages/core/src/config.ts` | `names` 数组、`.opencode` targets 与 basename 过滤（4 处） |
| `packages/core/src/skill/discovery.ts` | `.opencode-version` → `.tilacode-version`（2 处） |
| `packages/core/src/plugin/agent.ts` | plans 权限规则路径 |
| `packages/opencode/src/config/paths.ts` | `.opencode` targets（2 处） |
| `packages/opencode/src/config/config.ts` | `endsWith(".opencode")` |
| `packages/opencode/src/config/tui.ts` | `endsWith(".opencode")`（2 处） |
| `packages/opencode/src/config/tui-migrate.ts` | `opencode.json` 引用 |
| `packages/opencode/src/plugin/install.ts` | `.opencode` 目录 + patchName |
| `packages/opencode/src/session/session.ts` | `.opencode/plans` |
| `packages/opencode/src/plugin/tui/runtime.ts` | `.opencode` basename/themes/tui.json（3 处） |
| `packages/opencode/src/agent/agent.ts` | plans 权限规则路径 |
| `packages/opencode/src/cli/cmd/agent.ts` | agents 目录 |
| `packages/opencode/src/cli/cmd/mcp.ts` | 配置候选路径 |
| `packages/opencode/src/installation/index.ts` | `.opencode/bin` 检测 |
| `packages/opencode/src/cli/cmd/uninstall.ts` | `.opencode/bin` PATH 清理（4 处） |
| `packages/opencode/src/cli/cmd/serve.ts` | listening 输出行（协议） |
| `packages/opencode/src/server/**` | `x-opencode-directory` 头（workspace-routing 等） |
| 根目录 `install` | `INSTALL_DIR=$HOME/.tilacode/bin` |

### tilacode TS 源码（L3 环境变量）

- `packages/core/src/flag/flag.ts`：全部 `OPENCODE_*` → `TILACODE_*`（约 30 个）
- core / opencode / cli / tui / server 各包中所有 `Flag.OPENCODE_*` 读取方与 `process.env["OPENCODE_*"]` 直接引用方

### Python 桥接层

| 文件 | 变更点 |
|------|--------|
| `app/executor/workspace.py` | `.opencode` 目录、`opencode.jsonc` 文件名、`OPENCODE_CONFIG_TEMPLATE` 常量更名 |
| `app/executor/task_bridge.py` | plan 模式 prompt 文本路径 |
| `app/executor/tilacode_manager.py` | `OPENCODE_SERVER_PASSWORD`、listening 行解析正则、注释 |
| `app/executor/tilacode_client.py` | `x-opencode-directory` 头、Basic Auth 用户名 |

### 项目自身目录与测试

- tilacode 项目 `.opencode/` → `.tilacode/`（含 `opencode.jsonc` → `tilacode.jsonc`）
- tilacode 测试中 `.opencode` / `opencode.json(c)` / `OPENCODE_*` 引用（按失败测试逐批修复）
- Python `tests/test_workspace.py` / `test_task_bridge.py` / `test_tilacode_client.py` 同步

## 验收标准

1. **tilacode**：`bun run typecheck` 通过；配置发现、插件安装、skill 发现相关测试通过；`bun run script/build.ts` 成功产出二进制
2. **Python**：`pytest` 全量通过
3. **协议端到端**：新二进制 + 新 Python 层联调，plan 任务产物出现在 `.tilacode/plans/` 并被正确上报
4. **品牌验收**：在工作目录执行任务后，目录树中不存在 `.opencode/`；tilacode 进程环境变量中不存在 `OPENCODE_*` 前缀项
