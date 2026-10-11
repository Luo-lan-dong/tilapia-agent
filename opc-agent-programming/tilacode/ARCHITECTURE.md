# tilacode 架构概览

本文档梳理 tilacode 的整体架构、packages 结构、核心模块职责、数据流与关键概念。tilacode 基于 opencode（MIT 协议）二次整理，作为 OPC Agent 的编程执行层使用。

- 相关决策文档：`docs/adr/012-tilacode-execution-layer-integration.md`
- 对接改造指南：[INTEGRATION.md](./INTEGRATION.md)
- HTTP API 清单：[API.md](./API.md)

## 1. 技术栈与运行时

| 项 | 选型 |
|----|------|
| 语言 | TypeScript（ESM） |
| 运行时 | Bun 1.3.14（首选）/ Node.js（部分包） |
| 包管理 | Bun workspaces（catalog 版本统一管理） |
| 函数式框架 | Effect 4.0.0-beta（Schema、Layer、Stream、HttpApi） |
| HTTP 框架 | Effect HttpApi + HttpRouter（基于 Hono 风格） |
| 数据库 | SQLite（Drizzle ORM，Effect SQL 适配） |
| LLM SDK | AI SDK 6.x（Vercel）+ 自研 `@opencode-ai/llm` |
| UI | SolidJS（Web/App）、opentui（TUI） |
| 测试 | Bun test、Playwright（E2E） |
| 构建 | Bun script + Turbo（typecheck） |

入口命令：`tilacode`（[packages/opencode/bin/tilacode](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/opencode/bin/tilacode) 是平台二进制启动器）。

## 2. Monorepo 包结构

tilacode 是 Bun monorepo，所有子包位于 [packages/](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/) 下，内部包名统一为 `@opencode-ai/*`（保留原始命名，避免内部 import 大改）。根包 `tilacode`（[package.json](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/package.json)）是 CLI 入口包。

### 2.1 分层依赖关系

按 AGENTS.md 约定的依赖方向（不可逆转）：

```
Schema  ←  Core  ←  Protocol  ←  Server
                                  ↑
Schema  ←  Protocol  ←  Client  ←  SDK-Next
                                  ↑
                                 SDK（生成产物）
```

- Schema 是最底层，不依赖任何业务包。
- Client 不依赖 Core / Server，只依赖 Schema 和 Protocol。
- SDK-Next 在 Client 之上组合 Core + Server，提供进程内嵌入式宿主。

### 2.2 包清单

| 包 | 名称 | 职责 |
|----|------|------|
| [packages/schema](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/schema) | `@opencode-ai/schema` | 公共数据模型 Schema（Session、Message、Agent、Model、Event 等）。轻量级，不依赖数据库/Drizzle/WASM。 |
| [packages/protocol](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol) | `@opencode-ai/protocol` | HTTP API 路由定义（HttpApi、HttpApiGroup、HttpApiEndpoint）。每个 group 对应一组 REST 端点。 |
| [packages/core](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/core) | `@opencode-ai/core` | 核心业务逻辑：Session 执行、Agent、Tool、Config、Database、Provider、Permission、PTY、Filesystem。 |
| [packages/server](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/server) | `@opencode-ai/server` | HTTP Server 实现：handlers、middleware、auth、location。组装 Protocol + Core。 |
| [packages/client](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/client) | `@opencode-ai/client` | 从 Server HttpApi 生成的客户端。两个入口：`/`（Promise + fetch）、`/effect`（Effect + HttpClient）。 |
| [packages/sdk](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/sdk) | `@opencode-ai/sdk` | 旧版 JS SDK（生成产物）。提供给外部应用使用的客户端封装。 |
| [packages/sdk-next](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/sdk-next) | `@opencode-ai/sdk-next` | 新版 SDK（Embedded OpenCode）：进程内执行 Server 路由，无网络 I/O。 |
| [packages/opencode](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/opencode) | `tilacode` | 主包：CLI 入口、命令注册、集成所有包。包含 `serve`、`run` 等子命令。 |
| [packages/llm](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/llm) | `@opencode-ai/llm` | LLM 适配核心。Schema-first，统一 OpenAI/Anthropic/Gemini/Bedrock 等多 Provider 的事件流。 |
| [packages/cli](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/cli) | `@opencode-ai/cli` | CLI 公共工具（bin 启动器、tui 入口）。 |
| [packages/tui](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/tui) | `@opencode-ai/tui` | 终端 UI（基于 opentui/SolidJS）。 |
| [packages/app](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/app) | `@opencode-ai/app` | Web UI（Vite + SolidJS + Tailwind）。 |
| [packages/desktop](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/desktop) | `@opencode-ai/desktop` | 桌面应用（Electron 封装）。 |
| [packages/plugin](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/plugin) | `@opencode-ai/plugin` | 插件系统：工具、shell、tui 扩展点。 |
| [packages/codemode](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/codemode) | `@opencode-ai/codemode` | 受限 JavaScript 代码执行环境，模型可编写小程序通过工具调用完成任务。 |
| [packages/docs](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/docs) | `@opencode-ai/docs` | Mintlify 文档站源码 + openapi.json。 |
| [packages/function](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/function) | - | Cloudflare Worker 函数。 |
| [packages/script](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/script) | `@opencode-ai/script` | 共享构建脚本。 |
| [packages/enterprise](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/enterprise) | - | 企业版 UI。 |
| [packages/console](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/console) | - | 控制台 Web 应用。 |
| [packages/slack](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/slack) | - | Slack 集成。 |
| [packages/stats](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/stats) | - | 统计 UI。 |
| [packages/storybook](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/storybook) | - | UI 组件 Storybook。 |
| [packages/ui](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/ui) | `@opencode-ai/ui` | 共享 UI 组件库（Tailwind 主题）。 |
| [packages/identity](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/identity) | - | 品牌标识资源。 |
| [packages/http-recorder](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/http-recorder) | - | HTTP 请求录制工具（用于测试 cassette）。 |

## 3. 核心模块详解

### 3.1 Session 运行时（packages/core）

Session 是 tilacode 的核心抽象。一次会话从 `session.create` 开始，通过 `session.prompt` 接收用户输入，由 Session Runner 驱动 LLM Provider Turn 与 Tool 循环，最终输出 assistant 消息。

关键文件：
- [packages/core/src/session.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/core/src/session.ts) — Session 服务接口（`SessionV2.Service`）
- `packages/core/src/session/runner/` — Session Runner（drain 协调、provider turn 调度）
- `packages/core/src/session/execution/` — Session 执行协调器（process-local ownership）
- `packages/core/src/session/store.ts` — Session 持久化
- `packages/core/src/session/input.ts` — 输入准入（admit）
- `packages/core/src/session/projector.ts` — 消息投影
- `packages/core/src/session/revert.ts` — 会话回滚

### 3.2 关键概念（来自 CONTEXT.md）

- **System Context**：模型初始指令与运行时上下文更新（避免称"系统提示"）。
- **Session History**：经压缩与 Context Epoch 截断后投影给模型的对话。
- **Context Source**：System Context 的一个独立观测值（稳定 key + JSON codec + 渲染器）。
- **Context Epoch**：一段不可变 Baseline System Context 的有效期，止于压缩/移动/不兼容切换。
- **Admitted Prompt**：已持久化入 inbox 但尚未进入 Session History 的用户输入。
- **Prompt Promotion**：将 Admitted Prompt 提升为 Session History 的原子操作。
- **Provider Turn**：一次模型请求与其响应投影。
- **Session Drain**：进程内一次执行跨度，提升输入并运行 Provider Turn 直到无续行。
- **Mid-Conversation System Message**：运行时通知模型某 Context Source 变化的持久指令。
- **Embedded OpenCode**：进程内嵌入式宿主，复用 Client API + 内存 HTTP transport。

### 3.3 Tool 系统（packages/core/src/tool）

内置工具：bash、edit、glob、grep、read、write、skill、task 等。每个工具通过 ToolRegistry 注册，受 Permission 系统授权。模型 Tool Output 经 ToolRegistry 统一截断（行/字节限制），过大输出落到 Managed Tool Output File。

### 3.4 LLM 适配层（packages/llm）

Schema-first 设计，统一不同 Provider 的事件流。核心抽象：
- **Route**：Protocol + Endpoint + Auth + Framing 的组合（5-15 行即可接入新 Provider）。
- **Protocol**：语义 API 契约（body 构造、stream 事件 schema、状态机）。
- **Provider Facades**：`OpenAI.configure(...).responses("gpt-4o-mini")` 风格。
- **LLMEvent**：统一事件类型（textDelta、toolCall、finish 等）。

集成点：
- `packages/opencode/src/session/llm.ts` — Session 编排层，决定走 AI SDK 还是原生 Route。
- `packages/opencode/src/session/llm/native-request.ts` — 降级适配器。
- `packages/opencode/src/session/llm/native-runtime.ts` — 执行适配器。
- `packages/opencode/src/session/llm/ai-sdk.ts` — AI SDK 兼容路径。

### 3.5 配置系统（.opencode/）

配置目录 `.opencode/`（运行时由环境变量 `OPENCODE_DISABLE_PROJECT_CONFIG` 控制）：
- `opencode.jsonc` — 主配置（providers、agents、models、permissions）。
- `agent/` — 自定义 Agent 定义（markdown）。
- `command/` — 自定义斜杠命令。
- `skill/` — 技能定义。
- `tool/` — 工具扩展。
- `plugin/` — 插件脚本。
- `themes/` — TUI 主题。

## 4. 数据流

### 4.1 CLI 交互模式（默认）

```
用户终端
   │ tilacode "prompt"
   ▼
packages/opencode/src/index.ts (yargs CLI)
   │ RunCommand
   ▼
packages/opencode/src/cli/cmd/run.ts
   │ 创建 in-process Server（fetch 拦截）
   ▼
createOpencodeClient (packages/sdk/js/src/client.ts)
   │ HTTP（内存）请求
   ▼
packages/server/src/routes.ts (HttpApiBuilder)
   │ handlers
   ▼
packages/server/src/handlers/session.ts
   │ SessionHandler.session.prompt
   ▼
packages/core/src/session.ts (SessionV2.Service)
   │ admit → drain → runner
   ▼
packages/core/src/session/runner/
   │ Provider Turn + Tool 循环
   ▼
packages/llm (LLMClient.stream) ──► Provider API
   │
   ▼
SessionEvent (durable) ──SSE──► Client
   │
   ▼
run.ts loop() 输出到 stdout / TUI
```

### 4.2 HTTP Server 模式（OPC Agent 对接模式）

```
Python 外壳（WebSocket 心跳/注册）
   │ HTTP 请求
   ▼
tilacode serve（packages/opencode/src/cli/cmd/serve.ts）
   │ Server.listen(hostname, port)
   ▼
packages/server/src/routes.ts
   │ HttpApiBuilder.layer(Api)
   ▼
各 handlers/*（session/message/agent/model/...）
   │ 调用 Core 服务
   ▼
packages/core/src/* (SessionV2 / AgentV2 / ModelV2 / ...)
   │
   ▼
SQLite + Provider API
```

### 4.3 Session Prompt 完整流程

1. `POST /api/session/:id/prompt` 携带 `PromptInput`。
2. `SessionHandler.session.prompt` → `SessionV2.prompt`。
3. 持久化 `session_input`（Admitted Prompt）。
4. 除非 `resume: false`，调度 `SessionExecution.wake(sessionID)`。
5. Session Drain 启动：在 Safe Provider-Turn Boundary 提升输入。
6. 渲染 Baseline System Context（首次或 Context Epoch 切换）。
7. 调用 `llm.stream(request)`（一次 Provider Turn）。
8. 流式 `LLMEvent` → `SessionEvent`（durable）→ SSE 推送。
9. 若有 tool_call → ToolRuntime.dispatch → tool result 入 history → 续行 Provider Turn。
10. 无续行 → Session 进入 idle，emit `session.status: idle`。

## 5. 关键设计约束

来自 [AGENTS.md](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/AGENTS.md) 与 [CONTEXT.md](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/CONTEXT.md)：

- **依赖方向不可逆**：Schema ← Core ← Protocol ← Server；Client 不依赖 Core/Server。
- **HttpApi 是权威源**：Server 的 `HttpApi` 决定 Client 能力，codegen 从其生成。
- **一次 Provider Turn 一个 `llm.stream` 调用**：不通过 legacy loop 委托编排。
- **Session Execution 进程全局**：基于 Session ID，不传 Session ID 给下层。
- **Location 作用域**：SessionRunner、Tool Registry、Permission、Filesystem 均 Location-scoped。
- **durable 与 live 分离**：`sessions.events` 是 durable 可重放流；`events.subscribe` 是 live 不可重放。
- **Embedded OpenCode 复用 Client**：内存 HTTP transport，不绕过 routing/middleware/codec。
- **代码风格**：no `any`、no alias import、no star import、prefer const、early return、Effect Schema 优先。

## 6. 开发命令

```bash
# 安装依赖
bun install

# 启动 TUI 开发模式（默认）
bun run dev

# 启动 Web UI
bun run dev:web

# 类型检查（在子包目录运行，不可在根目录）
cd packages/opencode && bun typecheck

# 测试（在子包目录运行）
cd packages/opencode && bun test

# 启动 HTTP Server（OPC Agent 对接用）
bun run --cwd packages/opencode src/index.ts serve --hostname 0.0.0.0 --port 4096

# 非交互式执行单条 prompt（JSON 事件流）
bun run --cwd packages/opencode src/index.ts run "你的问题" --format json
```

## 7. 相关文档

- [README.md](./README.md) — 项目说明
- [API.md](./API.md) — HTTP API 端点清单
- [INTEGRATION.md](./INTEGRATION.md) — OPC Agent 对接改造指南
- [INDEX.md](./INDEX.md) — 文档索引
- [AGENTS.md](./AGENTS.md) — 开发规范（英文，原始）
- [CONTEXT.md](./CONTEXT.md) — Session 运行时概念（英文，原始）
- [CONTRIBUTING.md](./CONTRIBUTING.md) — 贡献指南
- `specs/v2/` — V2 技术规格（session/tools/config/provider 等）
- `docs/adr/012-tilacode-execution-layer-integration.md` — 集成决策记录（项目根 docs）
