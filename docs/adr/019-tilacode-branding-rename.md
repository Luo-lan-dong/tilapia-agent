# ADR-019：tilacode 品牌化路径重命名（.opencode → .tilacode）

## 状态

Accepted

## 日期

2026-09-02

## 问题背景

tilacode（原 opencode，MIT 协议）已按 ADR-012 集成为编程 Agent 执行层、按 ADR-013 以预编译二进制分发。项目此前已完成**命令层**重命名（bin 命令 `tilacode`、package 名 `tilacode`），但**路径体系仍是 opencode**：

* 工作目录内创建 `.opencode/` 配置目录（权限配置、plans 产物）

* 配置文件名 `opencode.json` / `opencode.jsonc`

* 环境变量前缀 `OPENCODE_*`（如 `OPENCODE_SERVER_PASSWORD`）

* 全局数据目录 `~/.local/share/opencode` 等（`packages/core/src/global.ts` 中 `const app = "opencode"`）

用户需求：**对外品牌统一**——终端用户在项目工作目录中不应看到 opencode 痕迹。当前处于内部开发阶段、未对外开放，**无历史数据迁移负担**。

### ADR-012 的冲突

ADR-012 §9 决策「无需修改 tilacode 源码」。本决策**部分推翻该条**：品牌化必然要求修改 tilacode 源码。ADR-012 其余决策（HTTP Server 集成模式、任务桥接层、Session 生命周期、权限策略）不受影响，但其中所有涉及 `.opencode` / `opencode.jsonc` / `OPENCODE_*` 的路径描述自本决策生效起变更为 tilacode 命名。

## 决策

### 1. 改名范围：L1 + L2 + L3，硬切不兼容

| 层级       | 变更                                                                      | 示例                                                        |
| -------- | ----------------------------------------------------------------------- | --------------------------------------------------------- |
| L1 目录名   | `.opencode` → `.tilacode`                                               | 工作目录配置目录、`~/.opencode/bin`、全局数据目录 `opencode` → `tilacode` |
| L2 配置文件名 | `opencode.json` / `opencode.jsonc` → `tilacode.json` / `tilacode.jsonc` | 含 tilacode 源码中的配置发现、插件配置 patch、tilacode 项目自身配置            |
| L3 环境变量  | `OPENCODE_*` → `TILACODE_*`                                             | `TILACODE_SERVER_PASSWORD`、`TILACODE_CONFIG_DIR` 等全部 flag |

**硬切**：目录/文件查找逻辑只认新命名，不回退兼容旧 `.opencode`。理由：内部开发阶段无存量数据；避免双路径分支逻辑增加维护成本与上游同步冲突。

### 2. 明确保留不改（防止改坏）

| 保留项                                                                  | 位置                             | 原因                                                                                         |
| -------------------------------------------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------ |
| `@opencode-ai/*` npm 包名                                              | workspace 依赖                   | 纯内部标识，对外不可见；改名涉及 200+ 文件，上游同步成本不可接受                                                        |
| `models.opencode.ai`、`console.opencode.ai`、`opencode.ai` 云端 URL      | models-dev、provider 等          | 真实在线服务（免费模型 MiMo 来源，ADR-012 定稿），改了会坏                                                       |
| provider ID `"opencode"`                                             | 模型标识 `opencode/mimo-v2.5-free` | 模型路由标识，指向 opencode 云端网关                                                                    |
| `$schema: https://opencode.ai/config.json`                           | 配置模板                           | 官方 JSON schema 地址                                                                          |
| LLM 网关请求头 `x-opencode-session` 等                                     | `session/llm/request.ts`       | 发往 opencode.ai 云端，属服务端协议                                                                   |
| 内部 UI 头 `x-opencode-ticket` / `x-opencode-sync` / `x-opencode-title` | pty/websocket 内部               | 不可见、与集成协议无关，降低改动面                                                                          |
| 根目录 `install` 脚本的下载 URL / 产物名                                        | `opencode-$target.tar.gz`      | 该脚本不在部署链路上（ADR-013 走中枢 `static/binaries/` 分发），仅将 `INSTALL_DIR` 品牌化为 `~/.tilacode/bin` 以求一致 |

### 3. 协议标识同步改名（跨端联动，必须成对修改）

以下 3 处是 Python 桥接层与 tilacode server 之间的**运行时协议**，改名必须两端同步，否则程序直接失效：

| 协议项                       | 旧                                         | 新                                         |
| ------------------------- | ----------------------------------------- | ----------------------------------------- |
| server 启动输出行（Python 解析端口） | `opencode server listening on http://...` | `tilacode server listening on http://...` |
| 工作目录路由头                   | `x-opencode-directory`                    | `x-tilacode-directory`                    |
| Basic Auth 密码环境变量         | `OPENCODE_SERVER_PASSWORD`                | `TILACODE_SERVER_PASSWORD`                |

### 4. 实施顺序

后端（tilacode TS 源码 + Python 桥接层）先行，验证通过后无需前端对接（本次变更不涉及中枢前端）。

## 备选方案

| 方案                                 | 优势               | 劣势                             | 结论         |
| ---------------------------------- | ---------------- | ------------------------------ | ---------- |
| **A（本决策）：L1+L2+L3 硬切**             | 对外品牌彻底统一；无兼容分支逻辑 | 改动约 40+ 源文件；上游同步冲突增加           | ✅ 选中（用户确认） |
| B：仅改 L1 目录名                        | 改动最小             | 配置文件名、环境变量仍是 opencode，品牌不彻底    | ❌ 否决       |
| C：L1\~L4 全改（含 npm 包名）              | 最彻底              | 200+ 文件，收益为零（包名不可见），上游同步成本不可接受 | ❌ 否决       |
| D：双目录兼容（优先 .tilacode 回退 .opencode） | 存量工作目录无痛迁移       | 双路径分支逻辑长期存在；内部阶段无此需求           | ❌ 否决       |

## 决策原因

1. **对外品牌统一**是用户的明确诉求，L1+L2+L3 恰好覆盖全部用户可见面
2. **内部开发阶段**是硬切的唯一窗口期——对外开放后再改需承担数据迁移
3. 保留 L4/L5 把改动面控制在可维护范围，避免为不可见的标识付出上游同步代价
4. 协议标识（listening 行、directory 头、密码 env）两端成对修改，风险可控

## 影响

### 对 tilacode 源码的影响

* 全局数据目录变更：`~/.local/share/opencode` → `~/.local/share/tilacode`（含 SQLite 会话库）；**旧目录数据不会被读取**，开发节点需手动迁移或接受重置

* 配置发现逻辑（core/config.ts、config/paths.ts、config/config.ts、config/tui.ts）全部改判 `.tilacode`

* skill 版本标记文件 `.opencode-version` → `.tilacode-version`

* tilacode 项目自身的 `.opencode/` 目录重命名为 `.tilacode/`，其中 `opencode.jsonc` → `tilacode.jsonc`

* flag.ts 中约 30 个 `OPENCODE_*` flag 全部更名，所有读取方同步

### 对 Python 桥接层的影响

* `workspace.py`：创建 `.tilacode/tilacode.jsonc`，读取 `.tilacode/plans/`

* `task_bridge.py`：plan 模式 prompt 文本中的产物路径

* `tilacode_manager.py`：注入 `TILACODE_SERVER_PASSWORD`、解析 `tilacode server listening` 行

* `tilacode_client.py`：携带 `x-tilacode-directory` 头、Basic Auth 用户名 `tilacode`

### 对部署的影响

* 需**重新构建二进制**并重新分发（ADR-013 链路），旧二进制与新 Python 层不兼容（协议标识变了）

* 已部署开发节点的旧会话数据（`~/.local/share/opencode`）不再可见；如需保留，手动 `mv ~/.local/share/opencode ~/.local/share/tilacode`（含 config/cache/state 同理）

### 对上游同步的影响

* 后续从 opencode 上游合并更新时，涉及上述文件的改动冲突面扩大；合并时需按本 ADR 的保留清单（§2）甄别哪些 opencode 字样必须还原为 tilacode

### 对 ADR-012 的影响

ADR-012 §9「无需修改 tilacode 源码」及全文中 `.opencode` / `opencode.jsonc` / `OPENCODE_SERVER_PASSWORD` 的描述自本决策生效起按新命名理解，不再回改 ADR-012 原文（历史决策保持原貌，以本 ADR 为准）。

## 关联文档

* [ADR-012 基于 tilacode 集成编程执行层](./012-tilacode-execution-layer-integration.md)

* [ADR-013 编程 Agent 预编译二进制分发协议](./013-programming-agent-binary-distribution.md)

* [019-tilacode-branding-spec.md](../spec/019-tilacode-branding-spec.md)

* [019-tilacode-branding-harness.md](../spec/019-tilacode-branding-harness.md)

* [019-tilacode-branding-changes.md](../spec/019-tilacode-branding-changes.md)

