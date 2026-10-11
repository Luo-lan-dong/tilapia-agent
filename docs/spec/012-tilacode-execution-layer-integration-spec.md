# tilacode 执行层集成 Spec

## 1. 功能描述

将 tilacode（原 opencode，MIT 协议的开源 AI 编程代理）以 HTTP Server 模式集成到 OPC Agent 内部，作为编程任务的执行层。Python 外壳保持现有的注册/心跳/WebSocket 通信能力不变，新增"任务桥接层"负责：

1. 接收中枢下发的编程任务，转换为 tilacode 的 session.create + session.prompt 调用
2. 订阅 tilacode 的执行事件流，转换为中枢的 agent.task_progress 上报
3. 管理工作目录（每任务独立目录，产物上传但不清理）
4. 管理 tilacode server 进程生命周期（启动、健康检查、自动重启）

### MVP 范围

- ✅ tilacode server 进程管理（启动/停止/健康检查）
- ✅ 任务桥接：WebSocket 指令 → HTTP API 调用 → 事件流转换 → 进度上报
- ✅ 工作目录管理（创建、产物打包上传）
- ✅ 完整编程能力（读写文件、执行 bash、多轮 LLM 对话、生成 diff）
- ✅ 会话续接（基于 session_id 的多次修改）
- ❌ LLM 配置中枢下发（后期迭代）
- ❌ 沙箱隔离（后期迭代）
- ❌ 工作目录自动清理（后期迭代）

## 2. 用户故事

- 作为中枢，我可以向编程 Agent 下发编程任务（含需求文档/prompt），Agent 自主完成代码编写
- 作为中枢，我可以实时收到 Agent 的执行进度（工具调用、文本输出、思考过程）
- 作为中枢，我可以在任务完成后收到代码产物打包，并在前端展示
- 作为中枢，我可以对不达标的任务下发修改指令，Agent 在原工作目录续接修改
- 作为中枢，我可以中断正在执行的任务
- 作为运维，tilacode server 崩溃后 Python 层自动重启并恢复

## 3. 核心流程

### 3.1 Agent 启动流程

```
Python Agent 启动
    ↓
注册到中枢（复用 ADR-008 流程）
    ↓
建立 WebSocket 心跳（复用 ADR-008 流程）
    ↓
启动 tilacode server 子进程（监听 localhost:4096）
    ↓
健康检查 tilacode server 就绪
    ↓
标记节点状态为 idle，等待任务
```

### 3.2 任务执行流程

```
中枢发送 hub.task_dispatch（含 task_id、prompt、文档、mode）
    ↓
Python 桥接层接收任务
    ↓
创建工作目录 /workspace/{task_id}/（已存在则复用）
    ↓
如有文档，写入工作目录
    ↓
调用 tilacode: POST /api/session（id=ses_task_<task_id>，指定 directory）
    ↓
如 mode 与当前 agent 不一致：POST /api/session/:id/agent 切换
    ↓
调用 tilacode: POST /api/session/:id/prompt（发送编程 prompt）
    ↓
订阅 tilacode: GET /api/session/:id/event（SSE）
    ↓
循环消费事件：
    ├── session.next.text.started/delta/ended → 上报 agent.task_progress（文本输出）
    ├── session.next.reasoning.started/delta/ended → 上报 agent.task_progress（思考过程，可选）
    ├── session.next.tool.called/progress/success/failed → 上报 agent.task_progress（工具调用）
    ├── session.next.step.ended → provider turn 完成，触发完成判定
    ├── session.error → 上报 task_failed，结束
    └── session.next.step.failed → 上报 task_failed，结束
    ↓
任务完成判定：
    ├── 观察到 session.next.step.ended 后一段时间内无新 step.started
    ├── 或轮询 GET /api/session/active、GET /api/session/:id/history 确认
    ↓
任务完成：
    ├── 打包工作目录产物
    ├── 上传中枢（复用文件存储 API）
    └── 上报 agent.task_result + task_completed
    ↓
工作目录保留（不清理，支持续接修改）
```

> **说明**：`session.status` 不是 tilacode server SSE 事件；`POST /api/session/:id/wait` 当前不可用（返回 503），因此不依赖它们判定完成。

### 3.3 任务续接修改流程

```
中枢发送 hub.task_dispatch（含原 task_id、修改指令、mode）
    ↓
Python 桥接层检测到 /workspace/{task_id}/ 已存在
    ↓
读取本地 sessions.json 获取 session_id（ses_task_<task_id>）
    ↓
调用 tilacode: POST /api/session（id=ses_task_<task_id>）
    ↓
如 session 存在则复用，不存在则新建（同一工作目录）
    ↓
如 mode 与当前 agent 不一致：POST /api/session/:id/agent 切换
    ↓
调用 tilacode: POST /api/session/:id/prompt（发送修改指令）
    ↓
订阅事件流，同 3.2
    ↓
执行完成，产物打包上传
```

### 3.4 任务中断流程

```
中枢发送 hub.task_interrupt（含 task_id）
    ↓
Python 桥接层调用 tilacode: POST /api/session/{session_id}/interrupt
    ↓
等待 SSE 中 session.next.step.ended 或短暂超时
    ↓
上报 agent.task_result（status=interrupted）
    ↓
工作目录保留
```

### 3.5 tilacode server 健康检查

```
Python 层每 30 秒健康检查 tilacode server
    ↓
GET http://localhost:{TILACODE_PORT}/api/health
    ↓
├── 健康 → 继续运行
└── 不健康/超时 → 重启 tilacode server
    ↓
重启后恢复心跳，标记当前执行中的任务为 failed（需中枢重试）
```

## 4. 降级链路

| 异常场景 | 降级策略 |
|---------|---------|
| tilacode server 启动失败 | Agent 标记为 unavailable，不上报 idle，中枢不分配任务 |
| tilacode server 运行中崩溃 | Python 层自动重启；当前任务标记 failed 上报中枢；中枢可重试 |
| session.prompt 调用超时 | 上报 task_progress（warning），等待或中断 |
| session.events 流断开 | 重连 tilacode，恢复事件订阅；如 session 已 idle 则正常结束 |
| 工作目录磁盘空间不足 | 上报 task_failed（原因：disk_full），拒绝新任务 |
| tilacode LLM API Key 无效 | 上报 task_failed（原因：llm_config_error），提示配置 LLM |
| 产物打包/上传失败 | 重试 3 次；仍失败则上报 task_completed 但标注"产物上传失败" |
| tilacode 二进制升级/重装 | Session 数据在 `~/.local/share/opencode/`（SQLite），与二进制目录分离，升级仅替换二进制不影响历史会话；升级流程必须保留 `data/sessions.json`、`workspace/`，且不更换 systemd 运行用户 |
| Python 层重启 | tilacode server 作为子进程被终止；重启后检测未完成的工作目录，上报中枢待决策 |

## 5. 验收标准

- [ ] Agent 启动时自动拉起 tilacode server，健康检查通过后标记为 idle
- [ ] 中枢下发编程任务后，Agent 在工作目录创建 session 并开始执行
- [ ] 执行过程中，中枢实时收到 agent.task_progress 事件（工具调用、文本输出）
- [ ] 任务完成后，中枢收到 task_completed 事件和产物包
- [ ] 中枢下发修改指令时，Agent 在原工作目录续接修改
- [ ] 中枢发送中断指令后，Agent 停止执行并上报 interrupted 状态
- [ ] tilacode server 崩溃后，Python 层自动重启并恢复心跳
- [ ] tilacode server 启动失败时，Agent 标记为 unavailable，不接收任务

## 6. 接口清单

### 6.1 Python 桥接层调用 tilacode 的 HTTP API

所有调用目标为 `http://localhost:{TILACODE_PORT}`（tilacode manager 启动时动态解析端口）。Base URL 前缀为 `/api`。

#### 6.1.1 创建/复用会话

```http
POST /api/session
Content-Type: application/json
Authorization: Basic <base64(opencode:password)>
X-Opencode-Directory: <url-encoded-workspace-path>

{
  "id": "ses_task_<task_id>",
  "agent": "build",
  "model": { "providerID": "opencode", "id": "mimo-v2.5-free" },
  "location": { "directory": "/workspace/{task_id}" }
}
```

> 使用相同 `id` 重复调用会返回已有 Session，实现续接。

#### 6.1.2 切换 agent

```http
POST /api/session/{sessionID}/agent
Content-Type: application/json
Authorization: Basic <base64(opencode:password)>

{
  "agent": "plan"
}
```

#### 6.1.3 发送 prompt

```http
POST /api/session/{sessionID}/prompt
Content-Type: application/json
Authorization: Basic <base64(opencode:password)>

{
  "prompt": {
    "text": "编程任务描述...",
    "files": [
      { "uri": "file:///workspace/{task_id}/spec.md", "name": "spec.md" }
    ],
    "agents": [{ "id": "explore" }]
  },
  "delivery": "steer",
  "resume": true
}
```

#### 6.1.4 订阅 Session 事件流

```http
GET /api/session/{sessionID}/event?after=<seq>
Accept: text/event-stream
Authorization: Basic <base64(opencode:password)>
```

返回 SSE 流，事件类型包括：

- `session.next.prompt.admitted`
- `session.next.step.started` / `ended` / `failed`
- `session.next.text.started` / `delta` / `ended`
- `session.next.reasoning.started` / `delta` / `ended`
- `session.next.tool.input.started` / `ended` / `called` / `progress` / `success` / `failed`
- `session.error`

> `session.status` 不会从此端点返回。

#### 6.1.5 订阅实例级事件流（权限事件）

```http
GET /api/event
Accept: text/event-stream
Authorization: Basic <base64(opencode:password)>
```

返回 SSE 流，包含 `permission.v2.asked` 等实例级事件。build 模式配置层全权赋予后通常不会收到，但 Python 层仍需订阅作为兜底。

#### 6.1.6 回复权限请求

```http
POST /api/session/{sessionID}/permission/{requestID}/reply
Content-Type: application/json
Authorization: Basic <base64(opencode:password)>

{
  "reply": "once"
}
```

`reply` 取值：`once` | `always` | `reject`。

#### 6.1.7 中断会话

```http
POST /api/session/{sessionID}/interrupt
Authorization: Basic <base64(opencode:password)>
```

#### 6.1.8 获取会话历史

```http
GET /api/session/{sessionID}/history?limit=50
Authorization: Basic <base64(opencode:password)>
```

### 6.2 中枢 WebSocket 事件扩展

在现有 `hub.task_dispatch` 事件基础上，扩展 payload：

```json
{
  "type": "hub.task_dispatch",
  "payload": {
    "task_id": "uuid",
    "task_name": "任务标题",
    "description": "任务描述",
    "mode": "plan|build",
    "specification": { "spec": "...", "tasks": "...", "checklist": "..." },
    "base_task_id": "uuid|null",
    "change_brief": "本轮修改说明（增量）|null"
  }
}
```

字段说明：

- `mode`：必填，`"plan"` 表示先做规划，`"build"` 表示直接开发
- `specification`：三件套（可为 null），Agent 写入工作目录 `docs/`
- `base_task_id`：可空，任务链根任务 ID；存在时 Agent **复用根任务的工作目录与 Session**（`ses_task_<base_task_id>`），链上任务共享 AI 上下文
- `change_brief`：可空，本轮修改说明（迭代增量 / 批准的方案全文 / 驳回反馈，由中枢拼装）；Agent prompt 显式携带，**不做文档 diff 检测**

Agent 上报事件复用现有协议：

```json
{
  "type": "agent.task_progress",
  "payload": {
    "task_id": "uuid",
    "progress": 50,
    "stage": "executing",
    "message": "正在编辑 src/main.py",
    "tool": { "name": "edit", "status": "completed" },
    "todos": [
      { "content": "验证 smali 文件修改", "status": "in_progress", "priority": "high" },
      { "content": "验证 APK 包含修改后的 dex 文件", "status": "pending", "priority": "medium" }
    ]
  }
}
```

`agent.task_progress` payload 字段说明：

- `task_id`：任务 ID，必填。
- `progress`：进度百分比 0~100，必填；当存在非空 `todos` 时由 Agent 按 todo 完成比例计算。
- `stage`：执行阶段，可选。
- `message`：人类可读进度说明，可选。
- `tool`：当前工具调用信息，可选。
- `todos`：当前会话待办列表，可选；存在时中枢以该列表作为真实进度与展示依据。

```json
{
  "type": "agent.task_result",
  "payload": {
    "task_id": "uuid",
    "status": "completed|failed|interrupted",
    "session_id": "tilacode session id，供续接使用",
    "plan_content": "plan 模式下 .opencode/plans/*.md 全文（markdown，可空）",
    "change_summary": "build 模式完成后 Agent 生成的本轮修改说明（markdown，可空）"
  }
}
```

> 实际事件名为中枢已实现的 `agent.task_completed`（`plan_content` 为 2026-08-15 增补字段，中枢存入 `Task.plan_content` 供审批；`change_summary` 为 2026-08-17 增补字段，中枢存入 `Task.change_summary`，见 ADR-012「中枢对话式评审界面与 change_summary 回传」增补决策）。

### 6.3 todo 列表实时上报与进度计算

#### 6.3.1 事件识别

tilacode 通过 `todowrite` 工具维护会话级待办列表，每次更新后在 Session 事件流中发布 `todo.updated` 事件：

```json
{
  "id": "evt_uuid",
  "type": "todo.updated",
  "data": {
    "sessionID": "ses_task_<task_id>",
    "todos": [
      { "content": "验证 smali 文件修改", "status": "in_progress", "priority": "high" },
      { "content": "验证 APK 签名有效性", "status": "pending", "priority": "medium" }
    ]
  },
  "durable": { "seq": 123 }
}
```

#### 6.3.2 进度计算策略

Agent `EventTranslator` 收到 `todo.updated` 后：

1. 如果 `todos` 非空，进度按 `completed / total * 100` 计算（向上取整到整数），并随 `agent.task_progress` 上报。
2. 如果 `todos` 为空或尚未收到任何 `todo.updated`，维持原有基于事件计数（工具调用/文本结束每次 +2，封顶 90）的进度机制。
3. 任务完成判定仍由 SSE 消费循环的 `step.ended + 静默复核` 触发，完成后统一置 100。

#### 6.3.3 中枢持久化

中枢 `Task` 模型新增 `todos` JSONB 列。`_handle_task_progress` 处理逻辑：

1. 校验 `task_id`、`progress` 与节点分配关系。
2. 如 payload 携带 `todos` 数组，写入 `task.todos`。
3. 当 `todos` 非空时，以 `completed / total` 计算值覆盖 payload 中的 `progress` 并存入 `task.progress`；否则直接使用 payload 中的 `progress`。
4. 前端通过既有 `GET /v1/tasks/{id}` 轮询获取 `todos` 与 `progress`。

### 6.4 前端展示

#### 6.4.1 文档面板折叠

`TaskNew` 右侧文档面板（规范文档/任务清单/验收清单/方案）支持折叠为图标栏：

- 默认展开。
- 展开时显示完整 Tabs 与内容区。
- 收起时只显示一列垂直图标按钮，点击图标可展开并切换到对应 Tab。
- 折叠状态为本地 UI 状态，不涉及 URL 参数。

#### 6.4.2 任务摘要面板

在文档面板旁边（或折叠后腾出的右侧区域）新增「任务摘要」面板，仅在存在 `?task=<task_id>` 跟踪态时展示：

- 顶部显示任务状态、执行模式、进度条。
- 中部列出 `todos` 数组，按 `in_progress / pending / completed` 分组或按顺序展示。
- 当前执行项高亮显示。
- 数据来自 `useTaskPlanTracking` 的轮询结果，随 `agent.task_progress` 实时更新。

### 6.5 tilacode 改造点

经源码确认，tilacode 现有 HTTP API 已覆盖 MVP 需求，**无需修改 tilacode 源码**。改造点转为 Python 层实现和 Agent 模板配置：

| 实现项 | 目的 | 优先级 |
|--------|------|--------|
| `.opencode/opencode.jsonc` 默认配置 | build 模式全权赋予 + 默认模型 MiMo V2.5 | 高 |
| Python 层 tilacode 进程管理 | 启动、健康检查、自动重启 | 高 |
| Python 层 tilacode HTTP 客户端 | 封装 `create_session`、`prompt`、`subscribe_events` 等 | 高 |
| Python 层事件转换器 | `session.next.*` 事件 → `agent.task_progress` | 高 |
| Python 层工作目录管理 | 创建、写入文档、打包产物 | 高 |
| Python 层 Session 映射管理 | `task_id → session_id` 的 `sessions.json` 维护 | 高 |
| （可选）产物打包端点 | 如 Python 层打包不便，再考虑在 tilacode 中新增 | 低 |

## 7. 项目结构

### 7.1 Python Agent 新增模块

```
opc-agent-programming/
├── app/
│   ├── client/
│   │   └── hub_client.py          # 现有：WebSocket 心跳/注册
│   ├── executor/                   # 新增：执行层
│   │   ├── __init__.py
│   │   ├── tilacode_manager.py     # tilacode server 进程管理
│   │   ├── tilacode_client.py      # tilacode HTTP API 客户端
│   │   ├── task_bridge.py          # 任务桥接（WebSocket ↔ HTTP）
│   │   ├── event_translator.py     # tilacode 事件 → 中枢进度上报
│   │   ├── workspace.py            # 工作目录管理
│   │   └── session_store.py        # task_id → session_id 映射管理
│   ├── config.py                   # 更新：新增 tilacode 配置项
│   └── main.py                     # 更新：启动时拉起 tilacode
├── tilacode/                       # 现有：tilacode 项目代码
├── .opencode/                      # 新增：tilacode 默认配置
│   └── opencode.jsonc
├── data/                           # 新增：Agent 本地数据
│   └── sessions.json
├── scripts/
│   └── install-agent-programming-linux.sh  # 已更新：拉取 tilacode 二进制
├── systemd/
│   └── opc-agent-programming.service       # 不变
├── pyproject.toml                  # 更新：新增 httpx/aiofiles 依赖
├── .env.example                    # 更新：新增 TILACODE_* 配置项
└── README.md
```

### 7.2 tilacode 改造文件

经源码确认，无需修改 tilacode 源码。只需在 Agent 模板中加入默认配置：

- `.opencode/opencode.jsonc`：默认权限策略、默认模型

如果后续发现 Python 层打包产物不方便，再评估是否在 tilacode 中新增产物打包端点。

## 8. 配置项

### 8.1 Python Agent 新增配置（.env）

```bash
# tilacode server 配置
TILACODE_PORT=4096
TILACODE_HOST=127.0.0.1
TILACODE_BINARY_PATH=./tilacode/node_modules/.bin/tilacode
TILACODE_WORKSPACE_ROOT=/workspace
TILACODE_HEALTH_CHECK_INTERVAL=30
TILACODE_AUTO_RESTART=true

# tilacode LLM 配置（MVP 阶段由 tilacode 自管，此处预留）
# TILACODE_LLM_PROVIDER=deepseek
# TILACODE_LLM_API_KEY=（后期由中枢下发）
# TILACODE_LLM_MODEL=deepseek-chat
```

### 8.2 tilacode 默认配置（.opencode/opencode.jsonc）

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "permission": {
    "*": "allow"
  },
  "model": "opencode/mimo-v2.5-free",
  "default_agent": "build"
}
```

说明：

- `"permission": { "*": "allow" }`：build 模式全权赋予，保证无人值守任务不被权限请求卡住
- `model`：默认使用 tilacode 自带的免费模型 MiMo V2.5 Free，格式为 `provider/model`。实际 id `mimo-v2.5-free` 已在部署节点通过 `GET /api/model` 实测核实（2026-08-14）；API 调用（session.create）中等价写法为 `{ "providerID": "opencode", "id": "mimo-v2.5-free" }`
- `default_agent`：默认 agent 为 `build`；plan 任务由 Python 层通过 `POST /api/session/:id/agent` 切换

## 9. 关联文档

- [ADR-012 基于 tilacode 集成编程执行层](../adr/012-tilacode-execution-layer-integration.md)
- [ADR-008 Linux 编程 Agent 节点 MVP 与节点管理](../adr/008-agent-node-linux-programming-mvp.md)
- [004-agent-node-protocol-spec.md](./004-agent-node-protocol-spec.md)
- [agent-node-standards.md](../standards/agent-node-standards.md)
- [api-standards.md](../standards/api-standards.md)
- [005-file-storage-spec.md](./005-file-storage-spec.md)
