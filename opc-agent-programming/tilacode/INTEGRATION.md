# tilacode 改造对接指南

本文档说明如何改造 tilacode 暴露接口、如何与 Python 外壳（OPC Agent）对接、需要修改哪些文件。基于 ADR-012 决策（HTTP Server 模式集成）。

- 决策文档：`docs/adr/012-tilacode-execution-layer-integration.md`
- 架构概览：[ARCHITECTURE.md](./ARCHITECTURE.md)
- HTTP API 清单：[API.md](./API.md)

## 1. 对接架构总览

```
┌──────────────────────────────────────────────────────────────┐
│  OPC Agent 平台                                               │
│                                                                │
│  ┌─────────────────────┐    ┌──────────────────────────────┐ │
│  │  Python 外壳         │    │  tilacode server             │ │
│  │  (agent-node)        │    │  (Bun + Effect HttpApi)      │ │
│  │                      │    │                                │ │
│  │  - WebSocket 心跳    │◄──►│  HTTP API                     │ │
│  │  - 任务注册/接收     │    │  /api/session/*               │ │
│  │  - 交付物上报        │    │  /api/event (SSE)             │ │
│  │  - HTTP 调用 tilacode│───►│  /api/permission/*            │ │
│  │                      │    │  /api/session/:id/prompt      │ │
│  └─────────────────────┘    └──────────────────────────────┘ │
│                                          │                    │
│                                          ▼                    │
│                                 ┌────────────────────┐       │
│                                 │  LLM Provider API   │       │
│                                 │  (Anthropic/OpenAI) │       │
│                                 └────────────────────┘       │
└──────────────────────────────────────────────────────────────┘
```

**职责分工**：

| 层 | 职责 | 不做 |
|----|------|------|
| Python 外壳 | WebSocket 心跳、任务注册、任务接收、交付物上报、权限策略决策 | 直接调用 LLM、直接操作文件系统 |
| tilacode server | Session 管理、LLM 调用、Tool 执行、上下文压缩、SSE 事件推送 | 与 OPC 平台通信、任务调度 |
| LLM Provider | 模型推理 | - |

## 2. 启动与进程管理

### 2.1 启动命令

Python 外壳拉起 tilacode server 子进程：

```python
import subprocess

proc = subprocess.Popen(
    [
        "tilacode", "serve",
        "--hostname", "127.0.0.1",   # 仅本机访问
        "--port", "0",                # 随机端口，从 stdout 解析实际端口
    ],
    env={
        **os.environ,
        "OPENCODE_SERVER_PASSWORD": "<random-token>",  # 必须设置
        # Provider 凭据
        "ANTHROPIC_API_KEY": "...",
        # 禁用项目级配置加载（保证环境纯净）
        # "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    },
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
```

启动后 tilacode 会向 stdout 输出：

```
opencode server listening on http://127.0.0.1:54321
```

Python 解析该行获取实际端口。

### 2.2 关键启动参数

完整参数见 [packages/opencode/src/cli/cmd/serve.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/opencode/src/cli/cmd/serve.ts) 与 `withNetworkOptions`：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--hostname` | `0.0.0.0` | 监听地址 |
| `--port` | 随机 | 监听端口 |
| `--cwd` | 当前目录 | 工作目录 |

环境变量：

| 变量 | 说明 |
|------|------|
| `OPENCODE_SERVER_PASSWORD` | Basic Auth 密码（用户名固定 `opencode`） |
| `OPENCODE_SERVER_USERNAME` | Basic Auth 用户名（默认 `opencode`） |
| `OPENCODE_LOG_LEVEL` | 日志级别（DEBUG/INFO/WARN/ERROR） |
| `OPENCODE_PRINT_LOGS` | 打印日志到 stderr |
| `OPENCODE_PURE` | 禁用外部插件 |
| `OPENCODE_DISABLE_PROJECT_CONFIG` | 禁用项目级配置加载 |
| Provider 凭据 | `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / 等 |

### 2.3 进程生命周期

- Python 启动后保持 stdin/stderr pipe，监听子进程退出。
- 优雅退出：发送 SIGTERM，等待 server 关闭。
- 异常退出：Python 检测到子进程退出后，根据策略重启或上报错误。
- 健康检查：定期 `GET /api/health`。

## 3. 任务执行流程

### 3.1 完整流程

```
OPC 平台
   │ 1. WebSocket 下发任务（prompt + context + agent 配置）
   ▼
Python 外壳
   │ 2. POST /api/session（如需要新建）
   │ 3. POST /api/session/:id/prompt
   │ 4. GET /api/session/:id/event（SSE 后台消费）
   │    ├─ session.next.text.*            → 流式输出到 WebSocket
   │    ├─ session.next.tool.*            → 工具执行事件
   │    ├─ session.next.step.ended        → provider turn 完成
   │    ├─ session.error                  → 上报错误
   │ 5. GET /api/event（实例级 SSE，接收权限请求）
   │    └─ permission.v2.asked            → 决策后 POST /reply
   │ 6. 任务完成判定：观察 session.next.step.ended 后无新 step，
   │    或轮询 /api/session/active、/api/session/:id/history
   │ 7. GET /api/session/:id/context（可选，获取完整消息）
   ▼
交付物上报 → OPC 平台
```

### 3.2 创建 Session

```python
resp = requests.post(
    f"{BASE}/api/session",
    headers=HEADERS,
    json={
        "agent": "build",   # 或 "plan"（只读）
        "model": {          # 可选，覆盖默认模型
            "providerID": "anthropic",
            "id": "claude-sonnet-4-5",
        },
    },
)
session_id = resp.json()["data"]["id"]
```

复用 Session：使用相同 ID 调用 `POST /api/session` 会采纳既有 Session（详见 CONTEXT.md "Reusing a Session ID"）。

### 3.3 发送 Prompt

```python
resp = requests.post(
    f"{BASE}/api/session/{session_id}/prompt",
    headers=HEADERS,
    json={
        "prompt": {
            "text": user_prompt,
            # 可附加文件：
            # "files": [
            #     {"uri": "file:///abs/path/to/x.ts", "name": "x.ts", "description": "..."}
            # ],
            # 可调用子 Agent：
            # "agents": [{"id": "explore"}]
        },
        "delivery": "steer",  # 立即影响当前 turn
        "resume": True,       # admit + wake
    },
)
```

**幂等重试**：携带固定 `id`（消息 ID），重复请求不会产生重复消息。冲突时返回 `ConflictError`。

### 3.4 消费 SSE 事件流

```python
import json
import threading

def consume_session_events(session_id, on_event):
    url = f"{BASE}/api/session/{session_id}/event"
    with requests.get(url, headers=HEADERS, stream=True, timeout=None) as r:
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            on_event(event)
            # 一个 provider turn 完成；可保持连接等待下一个 prompt
            if event.get("type") == "session.next.step.ended":
                pass  # 结合业务逻辑判断任务是否整体完成

# 权限事件需订阅实例级 SSE
def consume_instance_events(on_event):
    url = f"{BASE}/api/event"
    with requests.get(url, headers=HEADERS, stream=True, timeout=None) as r:
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            on_event(event)

threading.Thread(target=consume_session_events, args=(session_id, handle_session_event), daemon=True).start()
threading.Thread(target=consume_instance_events, args=(handle_instance_event,), daemon=True).start()
```

### 3.5 处理权限请求

tilacode 在执行工具前会通过 SSE 推送 `permission.v2.asked` 事件。该事件属于**实例级事件**，需通过 `GET /api/event` 接收，Python 必须决策并回复：

```python
def handle_instance_event(event):
    if event["type"] == "permission.v2.asked":
        props = event["properties"]
        # 自定义策略：根据 permission 类型 + patterns 决策
        action = decide_permission(
            props["permission"],   # "bash" / "edit" / "write" / ...
            props["patterns"],     # ["命令/文件路径模式"]
            props["sessionID"],
        )
        requests.post(
            f"{BASE}/api/session/{props['sessionID']}/permission/{props['id']}/reply",
            headers=HEADERS,
            json={"reply": action},  # "once" / "always" / "reject"
        )
```

权限类型清单见 [packages/core/src/permission.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/core/src/permission.ts)。

### 3.6 中断执行

```python
# 用户取消或超时
requests.post(f"{BASE}/api/session/{session_id}/interrupt", headers=HEADERS)
```

中断是幂等的：idle 状态下中断为 no-op。

### 3.7 获取交付物

任务完成后获取完整对话作为交付物：

```python
# 方式 1：获取当前上下文消息（压缩后）
resp = requests.get(f"{BASE}/api/session/{session_id}/context", headers=HEADERS)
messages = resp.json()["data"]

# 方式 2：获取完整持久化历史
resp = requests.get(
    f"{BASE}/api/session/{session_id}/history",
    headers=HEADERS,
    params={"limit": 100},
)
events = resp.json()["data"]
has_more = resp.json()["hasMore"]

# 方式 3：获取单条消息
resp = requests.get(
    f"{BASE}/api/session/{session_id}/message/{message_id}",
    headers=HEADERS,
)
```

## 4. 改造 tilacode 暴露接口

如需调整 tilacode 行为，按以下层次修改。

### 4.1 修改 HTTP API 端点（新增/调整路由）

**改哪里**：

| 改动类型 | 文件 |
|---------|------|
| 新增 Group | [packages/protocol/src/groups/](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups) 新建 `<name>.ts`，并在 [api.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/api.ts) 注册 |
| 新增端点 | 对应 group 文件中 `HttpApiEndpoint.get/post(...)` |
| 端点逻辑 | [packages/server/src/handlers/](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/server/src/handlers) 对应 handler，在 [handlers.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/server/src/handlers.ts) 注册 |
| Schema | [packages/schema/src/](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/schema/src) 对应 schema 文件 |

**新增端点示例**：

```ts
// packages/protocol/src/groups/myfeature.ts
import { HttpApiEndpoint, HttpApiGroup } from "effect/unstable/httpapi"
import { Schema } from "effect"

export const MyFeatureGroup = HttpApiGroup.make("server.myfeature")
  .add(
    HttpApiEndpoint.get("myfeature.list", "/api/myfeature", {
      success: Schema.Struct({ data: Schema.Array(Schema.String) }),
    }),
  )
```

```ts
// packages/protocol/src/api.ts 中注册
import { MyFeatureGroup } from "./groups/myfeature"
// 在 makeApiFromGroup 中 .add(MyFeatureGroup.middleware(locationMiddleware))
```

```ts
// packages/server/src/handlers/myfeature.ts
import { HttpApiBuilder } from "effect/unstable/httpapi"
import { Api } from "../api"

export const MyFeatureHandler = HttpApiBuilder.group(Api, "server.myfeature", (handlers) =>
  Effect.gen(function* () {
    return handlers.handle("myfeature.list", Effect.fn(function* () {
      return { data: ["a", "b"] }
    }))
  }),
)
```

```ts
// packages/server/src/handlers.ts 中注册
import { MyFeatureHandler } from "./handlers/myfeature"
export const handlers = Layer.mergeAll(/* ... */, MyFeatureHandler)
```

### 4.2 修改 Session 行为（如自定义 Agent）

**改哪里**：

| 改动 | 文件 |
|------|------|
| 自定义 Agent | `.opencode/agent/<name>.md`（配置级，无需改代码） |
| Agent 注册逻辑 | [packages/core/src/agent.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/core/src/agent.ts) |
| Session Runner | `packages/core/src/session/runner/` |
| Tool 注册 | [packages/core/src/tool/tools.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/core/src/tool/tools.ts) |

### 4.3 自定义 Agent（配置级，推荐）

在项目 `.opencode/agent/` 下新建 markdown 文件即可注册自定义 Agent，无需改代码：

```markdown
---
mode: primary        # primary | subagent
description: OPC 编程执行 Agent
model: anthropic/claude-sonnet-4-5
tools:
  - read
  - edit
  - bash
  - glob
  - grep
  - write
permission:
  bash: allow        # allow | deny | ask
  edit: ask
---

你是 OPC Agent 的编程执行层。你的职责是...
```

通过 `POST /api/session` 时传 `agent: "opencode-coder"` 即可使用。

### 4.4 修改 LLM Provider 配置

**改哪里**：

| 改动 | 文件 |
|------|------|
| 新增 Provider Route | [packages/llm/src/providers/](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/llm/src/providers) |
| 配置级 Provider | `.opencode/opencode.jsonc` 的 `provider` 字段 |
| 凭据 | 环境变量 / `POST /api/credential` |

### 4.5 添加自定义 Tool

**改哪里**：

| 改动 | 文件 |
|------|------|
| 内置 Tool | [packages/core/src/tool/](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/core/src/tool) 新建 `<name>.ts` |
| 配置级 Tool | `.opencode/tool/<name>.ts` |
| 插件 Tool | [packages/plugin/src/](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/plugin/src) |

配置级 Tool 示例（`.opencode/tool/mytool.ts`）：

```ts
export default {
  name: "mytool",
  description: "查询 OPC 平台 API",
  parameters: z.object({ endpoint: z.string() }),
  execute: async (args) => {
    const resp = await fetch(`https://opc-platform/api/${args.endpoint}`)
    return await resp.json()
  },
}
```

## 5. 与 Python 外壳对接的约定

### 5.1 目录与配置

- tilacode server 启动时的工作目录即默认 Location。
- 如需操作多个项目目录，通过 `x-opencode-directory` 请求头切换，不要重启 server。
- 项目级配置 `.opencode/` 会自动加载，如需纯净环境设置 `OPENCODE_DISABLE_PROJECT_CONFIG=1`。

### 5.2 错误处理

- HTTP 4xx/5xx 响应体含 `error.name`，按 [API.md](./API.md) 第 1.5 节处理。
- SSE 流中的 `session.error` 事件携带错误详情，Python 应上报到 OPC 平台。
- 任务失败可通过 `session.next.step.failed` 或 `session.error` 事件识别，需重新创建 Session 或修复后继续。

### 5.3 并发与队列

- 一个 Session 同时只能有一个活跃 Drain（进程内 SessionExecution 协调）。
- 多个 prompt 并发提交：`delivery: "steer"` 会插入当前 turn；`delivery: "queue"` 会排队等 idle。
- 多个 Session 可并行执行（不同 sessionID）。
- 长任务建议：一个任务一个 Session，完成后归档。

### 5.4 资源清理

- 任务完成后可保留 Session 以便复用（`POST /api/session` 同 ID）。
- 长期不用的 Session 可不主动删除（SQLite 持久化，不影响内存）。
- 进程退出前发送 SIGTERM，让 tilacode 完成 cleanup（ToolOutputStore 清理等）。

### 5.5 日志与可观测性

- 设置 `OPENCODE_PRINT_LOGS=1` 将结构化日志输出到 stderr，Python 可重定向到文件。
- `OPENCODE_LOG_LEVEL=DEBUG` 用于排查问题。
- tilacode 内置 OpenTelemetry，可通过环境变量配置 OTLP exporter。

## 6. 改造检查清单

对接前确认以下事项：

- [ ] tilacode 已构建（`bun install` + 必要的 `bun run build`）
- [ ] `tilacode serve` 可正常启动并监听端口
- [ ] `GET /api/health` 返回 200
- [ ] `OPENCODE_SERVER_PASSWORD` 已设置（生产环境必须）
- [ ] Provider 凭据已通过环境变量或 `/api/credential` 配置
- [ ] `GET /api/provider` 返回期望的 Provider 列表
- [ ] `GET /api/model` 返回期望的 Model 列表
- [ ] `GET /api/agent` 返回期望的 Agent 列表（含自定义 Agent）
- [ ] Python 可创建 Session、发送 prompt、消费 SSE
- [ ] 权限请求回复链路通畅
- [ ] 中断接口可用
- [ ] 进程优雅退出（SIGTERM）正常

## 7. 常见问题排查

### 7.1 server 启动失败

- 检查端口占用：`netstat -ano | findstr :4096`
- 检查 Bun 版本：需 `bun@1.3.14`
- 检查 `bun install` 是否完成：`packages/core` 的 `postinstall` 会修复 node-pty

### 7.2 Provider 调用失败

- `GET /api/provider/:id` 查看配置
- 检查环境变量是否传到子进程
- 检查 `ANTHROPIC_API_KEY` 等 key 是否有效
- SSE `session.error` 事件会携带 provider 错误详情

### 7.3 工具执行被阻塞

- 检查 `permission.v2.asked` 事件是否被正确消费和回复（通过 `GET /api/event`）
- 默认非交互模式会自动 reject 某些权限（见 [run.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/opencode/src/cli/cmd/run.ts) 中 `rules` 定义）
- 可在 `.opencode/opencode.jsonc` 中配置 `permission` 规则预授权

### 7.4 SSE 连接断开

- `event.subscribe`（实例级）断线后无法重放，需刷新状态后重新订阅
- `session.events`（Session 级）支持 `after` 序列号重放，断线后用最后看到的序列号重连
- Python 应实现断线重连逻辑

### 7.5 内存/性能问题

- 长时间运行的 Session 会累积 history；当前 `POST /api/session/:id/compact` 不可用，可通过创建新 Session 或控制 prompt 长度管理上下文
- 多个并行任务用不同 Session，避免单 Session 队列阻塞
- 监控 tilacode 子进程内存，超出阈值时重启

## 8. 相关文档

- [ARCHITECTURE.md](./ARCHITECTURE.md) — 架构概览
- [API.md](./API.md) — HTTP API 完整清单
- [INDEX.md](./INDEX.md) — 文档索引
- [AGENTS.md](./AGENTS.md) — 原始开发规范
- [CONTEXT.md](./CONTEXT.md) — Session 运行时概念
- `docs/adr/012-tilacode-execution-layer-integration.md` — 集成决策记录
- `docs/spec/tilacode-execution-layer-integration-spec.md` — 集成规格
- `docs/spec/tilacode-execution-layer-integration-harness.md` — 测试清单
