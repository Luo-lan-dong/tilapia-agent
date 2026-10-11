# tilacode HTTP API 接口文档

本文档整理 tilacode server 暴露的 HTTP API 端点清单，供 Python 外壳（OPC Agent）对接参考。所有路由由 [packages/protocol/src/](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src) 定义，由 [packages/server/src/handlers/](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/server/src/handlers) 实现。

- 架构概览：[ARCHITECTURE.md](./ARCHITECTURE.md)
- 对接改造指南：[INTEGRATION.md](./INTEGRATION.md)
- OpenAPI Schema：启动 server 后访问 `http://<host>:<port>/openapi.json`

## 1. 通用约定

### 1.1 Base URL

```
http://<hostname>:<port>
```

默认端口 4096（可通过 `--port` 覆盖）。启动命令：

```bash
tilacode serve --hostname 0.0.0.0 --port 4096
```

### 1.2 认证

- 通过环境变量 `OPENCODE_SERVER_PASSWORD` 启用 Basic Auth。
- 未设置时 server 无认证（日志会输出 warning）。
- 请求头：`Authorization: Basic <base64(opencode:password)>`，用户名固定 `opencode`。
- 参见 [packages/server/src/auth.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/server/src/auth.ts)。

### 1.3 Location 路由

tilacode server 支持多工作目录实例。请求通过以下方式指定目标目录：

- 请求头 `x-opencode-directory: <url-encoded-abs-path>`（SDK 默认行为）
- 查询参数 `?directory=<abs-path>`（GET 请求兜底）

未指定时使用 server 启动时的工作目录。参见 [packages/server/src/location.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/server/src/location.ts)。

### 1.4 响应格式

- 成功：`{ "data": <payload> }` 或 `204 No Content`。
- 分页：`{ "data": [...], "cursor": { "previous": "<opaque>", "next": "<opaque>" } }`。
- 错误：`{ "error": { "name": "<ErrorName>", "message": "...", ...details } }`。
- SSE 流：`Content-Type: text/event-stream`，每行 `data: <json>\n\n`。

### 1.5 通用错误码

| 错误名 | HTTP 状态 | 含义 |
|--------|----------|------|
| `SessionNotFoundError` | 404 | Session 不存在 |
| `MessageNotFoundError` | 404 | 消息不存在 |
| `ConflictError` | 409 | 资源冲突（如 prompt ID 重复） |
| `InvalidCursorError` | 400 | 分页游标无效 |
| `InvalidRequestError` | 400 | 请求参数无效 |
| `ServiceUnavailableError` | 503 | 服务暂不可用（如未初始化完成） |
| `UnknownError` | 500 | 未知服务器错误（含 ref 用于日志追溯） |

完整错误定义见 [packages/protocol/src/errors.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/errors.ts)。

## 2. Session API（核心）

Group: `server.session`，定义于 [packages/protocol/src/groups/session.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/session.ts)，实现于 [packages/server/src/handlers/session.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/server/src/handlers/session.ts)。

| 方法 | 路径 | 操作名 | 说明 |
|------|------|--------|------|
| GET | `/api/session` | `session.list` | 列出 Session（支持分页/搜索/排序） |
| POST | `/api/session` | `session.create` | 创建 Session |
| GET | `/api/session/active` | `session.active` | 列出当前进程活跃的 Session |
| GET | `/api/session/:sessionID` | `session.get` | 获取 Session 详情 |
| POST | `/api/session/:sessionID/agent` | `session.switchAgent` | 切换 Agent |
| POST | `/api/session/:sessionID/model` | `session.switchModel` | 切换 Model |
| POST | `/api/session/:sessionID/prompt` | `session.prompt` | 发送消息（核心） |
| POST | `/api/session/:sessionID/compact` | `session.compact` | 压缩会话上下文（当前不可用，返回 503） |
| POST | `/api/session/:sessionID/wait` | `session.wait` | 等待 Session 进入 idle（当前不可用，返回 503） |
| POST | `/api/session/:sessionID/interrupt` | `session.interrupt` | 中断当前执行 |
| GET | `/api/session/:sessionID/context` | `session.context` | 获取当前上下文消息 |
| GET | `/api/session/:sessionID/history` | `session.history` | 获取持久化事件历史 |
| GET | `/api/session/:sessionID/event` | `session.events` | SSE 订阅 Session 事件流 |
| GET | `/api/session/:sessionID/message/:messageID` | `session.message` | 获取单条消息 |
| POST | `/api/session/:sessionID/revert/stage` | `session.revert.stage` | 暂存回滚点 |
| POST | `/api/session/:sessionID/revert/clear` | `session.revert.clear` | 清除暂存回滚 |
| POST | `/api/session/:sessionID/revert/commit` | `session.revert.commit` | 提交回滚 |

### 2.1 POST /api/session — 创建 Session

请求体：

```json
{
  "id": "sess_01HABC...",          // 可选，自定义 Session ID
  "agent": "build",                 // 可选，Agent ID（build/plan/general 或自定义）
  "model": {                        // 可选
    "providerID": "anthropic",
    "id": "claude-sonnet-4-5",
    "variant": "high"
  },
  "location": {                     // 可选，默认使用请求 directory
    "directory": "/abs/path/to/project"
  }
}
```

响应：`{ "data": Session.Info }`

### 2.2 POST /api/session/:sessionID/prompt — 发送消息（核心）

请求体：

```json
{
  "id": "msg_01HABC...",            // 可选，自定义消息 ID（用于幂等重试）
  "prompt": {                       // PromptInput，必填
    "text": "请帮我实现...",
    "files": [                      // 可选附件
      {
        "uri": "file:///abs/path/to/foo.ts",
        "name": "foo.ts",
        "description": "可选描述"
      }
    ],
    "agents": [                     // 可选子 Agent
      { "id": "explore" }
    ]
  },
  "delivery": "steer",              // 可选：steer（默认，立即影响当前 turn）| queue（排队等 idle）
  "resume": true                    // 可选：true（默认，admit + wake）| false（仅 admit 不执行）
}
```

响应：`{ "data": SessionInput.Admitted }`（包含准入的消息 ID）

执行流程参见 [ARCHITECTURE.md](./ARCHITECTURE.md) 第 4.3 节。

### 2.3 GET /api/session/:sessionID/event — SSE 事件流

查询参数：
- `after`（可选）：聚合序列号，从该序号之后开始重放持久化事件。

响应：SSE 流，每条 `data:` 行为一个 `SessionEvent.Durable` JSON。

关键事件类型（`type` 字段）：

本端点只返回 **Session 相关的 durable 事件**，不会返回权限事件。

V2 执行事件：
- `session.next.prompt.admitted` — prompt 已准入
- `session.next.step.started` — 开始一个 provider turn
- `session.next.text.started` / `delta` / `ended` — 文本输出
- `session.next.reasoning.started` / `delta` / `ended` — 推理过程
- `session.next.tool.input.started` / `ended` — 工具输入拼装
- `session.next.tool.called` — 工具被调用
- `session.next.tool.progress` — 工具执行进度
- `session.next.tool.success` / `failed` — 工具执行成功/失败
- `session.next.step.ended` / `failed` — 一个 provider turn 完成/失败

V1 兼容事件：
- `session.created` / `session.updated`
- `message.updated` — 消息更新
- `message.part.updated` — 消息分段更新
- `session.error` — 执行错误

> 注意：`session.status` 不是 server SSE 事件，不会从此端点发出。任务完成判定应观察 `session.next.step.ended` 或轮询 `/api/session/active`、`/api/session/:id/history`。

消费示例（Python）：

```python
import requests

resp = requests.get(
    f"http://{host}:{port}/api/session/{session_id}/event",
    headers={"Authorization": auth, "x-opencode-directory": dir_enc},
    stream=True,
)
for line in resp.iter_lines():
    if line.startswith(b"data: "):
        event = json.loads(line[6:])
        handle_event(event)
```

### 2.4 GET /api/session — 列出 Session

查询参数：
- `limit`（可选，默认 50）：返回数量上限。
- `order`（可选）：`asc`（最旧优先）/ `desc`（最新优先，首次查询默认）。
- `search`（可选）：标题模糊搜索。
- `directory` / `project` / `subpath`（可选）：作用域过滤。
- `cursor`（可选）：上次响应的 `cursor.previous` 或 `cursor.next`。

响应：

```json
{
  "data": [Session.Info, ...],
  "cursor": {
    "previous": "<opaque-cursor-or-null>",
    "next": "<opaque-cursor-or-null>"
  }
}
```

游标为不透明 base64url 字符串，不可解析，原样回传。

## 3. Message API

Group: `server.message`，定义于 [packages/protocol/src/groups/message.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/message.ts)。

| 方法 | 路径 | 操作名 | 说明 |
|------|------|--------|------|
| GET | `/api/session/:sessionID/message` | `session.messages` | 列出 Session 消息（分页） |

## 4. Event API（实例级实时流）

Group: `server.event`，定义于 [packages/protocol/src/groups/event.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/event.ts)。

| 方法 | 路径 | 操作名 | 说明 |
|------|------|--------|------|
| GET | `/api/event` | `event.subscribe` | 实例级 SSE 实时事件流（无重放保证） |

与 `session.events` 的区别：
- `event.subscribe` 是 live-only，包含连接/心跳/实例销毁事件，断线后无法重放。
- `session.events` 是 durable，支持 `after` 序列号重放。

## 5. Agent / Model / Provider API

| 方法 | 路径 | 操作名 | Group | 说明 |
|------|------|--------|-------|------|
| GET | `/api/agent` | `agent.list` | `server.agent` | 列出可用 Agent |
| GET | `/api/model` | `model.list` | `server.model` | 列出可用 Model |
| GET | `/api/provider` | `provider.list` | `server.provider` | 列出已配置 Provider |
| GET | `/api/provider/:providerID` | `provider.get` | `server.provider` | 获取单个 Provider 详情 |

定义文件：
- [packages/protocol/src/groups/agent.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/agent.ts)
- [packages/protocol/src/groups/model.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/model.ts)
- [packages/protocol/src/groups/provider.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/provider.ts)

## 6. Permission API

Group: `server.permission`，定义于 [packages/protocol/src/groups/permission.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/permission.ts)。

| 方法 | 路径 | 操作名 | 说明 |
|------|------|--------|------|
| GET | `/api/permission/request` | `permission.request` | 列出待处理权限请求 |
| GET | `/api/permission/saved` | `permission.saved.list` | 列出已保存权限规则 |
| DELETE | `/api/permission/saved/:id` | `permission.saved.remove` | 删除已保存权限规则 |
| GET | `/api/session/:sessionID/permission` | `session.permission.list` | 列出 Session 权限请求 |
| POST | `/api/session/:sessionID/permission/:requestID/reply` | `session.permission.reply` | 回复权限请求（once/always/reject） |

权限请求通过 SSE `permission.v2.asked` 事件下发。该事件属于**实例级事件**，需通过 `GET /api/event` 接收，不会从 `GET /api/session/:id/event` 返回。Python 层需监听 `/api/event` 并调用 `reply` 接口响应。

`reply` 取值：
- `once` — 仅允许一次
- `always` — 始终允许
- `reject` — 拒绝

## 7. Question API

Group: `server.question`，定义于 [packages/protocol/src/groups/question.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/question.ts)。

| 方法 | 路径 | 操作名 | 说明 |
|------|------|--------|------|
| GET | `/api/question/request` | `question.request` | 列出待处理问题 |
| GET | `/api/session/:sessionID/question` | `session.question.list` | 列出 Session 问题 |
| POST | `/api/session/:sessionID/question/:requestID/reply` | `session.question.reply` | 回答问题 |
| POST | `/api/session/:sessionID/question/:requestID/reject` | `session.question.reject` | 拒绝回答 |

## 8. FileSystem API

Group: `server.fs`，定义于 [packages/protocol/src/groups/fs.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/fs.ts)。

| 方法 | 路径 | 操作名 | 说明 |
|------|------|--------|------|
| GET | `/api/fs/read/*` | `fs.read` | 读取文件内容 |
| GET | `/api/fs/list` | `fs.list` | 列出目录 |
| GET | `/api/fs/find` | `fs.find` | 搜索文件 |

## 9. Command / Skill API

| 方法 | 路径 | 操作名 | Group | 说明 |
|------|------|--------|-------|------|
| GET | `/api/command` | `command.list` | `server.command` | 列出可用斜杠命令 |
| GET | `/api/skill` | `skill.list` | `server.skill` | 列出可用 Skill |

定义：
- [packages/protocol/src/groups/command.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/command.ts)
- [packages/protocol/src/groups/skill.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/skill.ts)

## 10. PTY API

Group: `server.pty`，定义于 [packages/protocol/src/groups/pty.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/pty.ts)。

| 方法 | 路径 | 操作名 | 说明 |
|------|------|--------|------|
| GET | `/api/pty` | `pty.list` | 列出 PTY 会话 |
| POST | `/api/pty` | `pty.create` | 创建 PTY 会话 |
| GET | `/api/pty/:ptyID` | `pty.get` | 获取 PTY 详情 |
| PUT | `/api/pty/:ptyID` | `pty.update` | 更新 PTY 配置 |
| DELETE | `/api/pty/:ptyID` | `pty.remove` | 删除 PTY 会话 |
| POST | `/api/pty/:ptyID/connect-token` | `pty.connectToken` | 生成连接 token |
| GET | `/api/pty/:ptyID/connect` | `pty.connect` | WebSocket 升级连接 |

## 11. Credential / Integration API

| 方法 | 路径 | 操作名 | Group | 说明 |
|------|------|--------|-------|------|
| PATCH | `/api/credential/:credentialID` | `credential.update` | `server.credential` | 更新凭据 |
| DELETE | `/api/credential/:credentialID` | `credential.remove` | `server.credential` | 删除凭据 |
| GET | `/api/integration` | `integration.list` | `server.integration` | 列出集成 |
| GET | `/api/integration/:integrationID` | `integration.get` | `server.integration` | 获取集成详情 |
| POST | `/api/integration/:integrationID/connect/key` | `integration.connectKey` | `server.integration` | API Key 连接 |
| POST | `/api/integration/:integrationID/connect/oauth` | `integration.connectOAuth` | `server.integration` | OAuth 连接 |
| GET | `/api/integration/attempt/:attemptID` | `integration.attempt.get` | `server.integration` | 获取 OAuth 流程状态 |
| POST | `/api/integration/attempt/:attemptID/complete` | `integration.attempt.complete` | `server.integration` | 完成 OAuth 流程 |
| DELETE | `/api/integration/attempt/:attemptID` | `integration.attempt.cancel` | `server.integration` | 取消 OAuth 流程 |

定义：
- [packages/protocol/src/groups/credential.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/credential.ts)
- [packages/protocol/src/groups/integration.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/integration.ts)

## 12. 其他 API

| 方法 | 路径 | 操作名 | Group | 说明 |
|------|------|--------|-------|------|
| GET | `/api/health` | `health.get` | `server.health` | 健康检查 |
| GET | `/api/location` | `location.get` | `server.location` | 获取当前 Location 信息 |
| GET | `/api/session/:sessionID/message` | `session.messages` | `server.message` | 列出 Session 消息 |
| GET | `/api/session/:sessionID/reference` | `session.reference.list` | `server.reference` | 列出 Session 引用 |
| POST | `/experimental/project/:projectID/copy` | `project.copy.create` | `server.project-copy` | 创建项目副本（实验性） |
| DELETE | `/experimental/project/:projectID/copy` | `project.copy.remove` | `server.project-copy` | 删除项目副本 |
| POST | `/experimental/project/:projectID/copy/refresh` | `project.copy.refresh` | `server.project-copy` | 刷新项目副本 |

定义：
- [packages/protocol/src/groups/health.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/health.ts)
- [packages/protocol/src/groups/location.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/location.ts)
- [packages/protocol/src/groups/reference.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/reference.ts)
- [packages/protocol/src/groups/project-copy.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/protocol/src/groups/project-copy.ts)

## 13. 典型对接流程（Python 外壳）

```python
import requests
import json

BASE = "http://localhost:4096"
AUTH = ("opencode", "<password>")
DIR = "/abs/path/to/project"
HEADERS = {
    "Authorization": "Basic " + base64.b64encode(f"opencode:<password>".encode()).decode(),
    "x-opencode-directory": requests.utils.quote(DIR, safe=""),
    "Content-Type": "application/json",
}

# 1. 健康检查
requests.get(f"{BASE}/api/health", headers=HEADERS)

# 2. 创建 Session
resp = requests.post(f"{BASE}/api/session", headers=HEADERS,
                     json={"agent": "build"})
session_id = resp.json()["data"]["id"]

# 3. 订阅 Session SSE 事件流（后台线程）
def consume_session_events():
    with requests.get(f"{BASE}/api/session/{session_id}/event",
                      headers=HEADERS, stream=True) as r:
        for line in r.iter_lines():
            if line.startswith(b"data: "):
                event = json.loads(line[6:])
                # 处理 session.next.* / message.part.updated / session.error
                handle_session_event(event)

# 4. 订阅实例级 SSE 以接收权限请求（如需要自动审批）
def consume_instance_events():
    with requests.get(f"{BASE}/api/event",
                      headers=HEADERS, stream=True) as r:
        for line in r.iter_lines():
            if line.startswith(b"data: "):
                event = json.loads(line[6:])
                if event.get("type") == "permission.v2.asked":
                    requests.post(
                        f"{BASE}/api/session/{event['sessionID']}/permission/{event['id']}/reply",
                        headers=HEADERS,
                        json={"reply": "once"},
                    )

# 5. 发送 prompt
requests.post(f"{BASE}/api/session/{session_id}/prompt",
              headers=HEADERS,
              json={"prompt": {"text": "..."}})

# 6. 等待任务完成
#    方式 A：观察到 session.next.step.ended 后一段时间内无新 step.started
#    方式 B：轮询 GET /api/session/active 或 /api/session/{session_id}/history
# 7. 中断（如需要）
requests.post(f"{BASE}/api/session/{session_id}/interrupt", headers=HEADERS)

# 7. 获取上下文/历史
requests.get(f"{BASE}/api/session/{session_id}/context", headers=HEADERS)
requests.get(f"{BASE}/api/session/{session_id}/history?limit=50",
             headers=HEADERS)
```

## 14. SDK 客户端

如需 TypeScript 客户端，使用 `@opencode-ai/sdk`：

```ts
import { createOpencodeClient } from "@opencode-ai/sdk/v2"

const client = createOpencodeClient({
  baseUrl: "http://localhost:4096",
  directory: "/abs/path/to/project",
  headers: { Authorization: "Basic ..." },
})

const session = await client.session.create({ agent: "build" })
await client.session.prompt({
  sessionID: session.data.id,
  prompt: { parts: [{ type: "text", text: "Hello" }] },
})
```

SDK 源码：[packages/sdk/js/src/client.ts](file:///d:/bianchen/tilapia-agent/opc-agent-programming/tilacode/packages/sdk/js/src/client.ts)。
