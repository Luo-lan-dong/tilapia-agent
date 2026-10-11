# 任务创建 Spec Chat 设计文档

## 1. 背景与问题

当前 OPC 中枢的「新建任务」功能只有一个自然语言输入框。用户输入一句话后，系统直接调用 Planning Agent 生成任务并落库。这种单轮输入导致：

- 用户意图容易被误解或丢失关键上下文。
- 缺少技术栈、优先级、验收标准等必要信息。
- 用户在任务创建前没有机会看到并修正 AI 生成的计划。

本设计参考 Trae `/spec` 模式，引入多轮对话 + 实时三件套文档预览的方式，让用户和 AI 共同把需求澄清到足够准确后，再创建任务。

## 2. 目标

- 通过多轮对话收集足够信息，降低任务理解偏差。
- 在创建任务前生成并展示 `spec.md` / `tasks.md` / `checklist.md` 三件套草案，用户可查看和编辑。
- 用户确认后，将三件套作为任务规范保存，任务进入任务队列。
- MVP 阶段支持用户在任务队列中手动指定子系统执行，并将规范文档推送给该子系统。
- MVP 阶段子系统只需接收文档并返回固定响应。

## 3. 非目标

- 本次设计不包含子系统真实执行能力（MVP 仅做接收+固定响应）。
- 不包含自动任务拆分后的动态子任务创建（三件套中的 `tasks.md` 是规划文档，不直接生成数据库子任务）。
- 不包含文件上传附件功能（如需可在后续迭代补充）。
- 不包含任务执行进度追踪（复用现有任务状态流转）。
- 历史会话全局可见，MVP 阶段不做用户隔离（后续接入用户体系后按 `user_id` 隔离）。

## 4. 用户流程

### 4.1 新建会话流程

```text
1. 用户在左侧边栏点击「新建任务」菜单。
2. 进入 /tasks/new 独立页面。
3. 页面顶部显示当前模型（如 deepseek-chat）和 fast/thinking 模式切换。
4. 左侧聊天区：用户输入需求，Spec Agent 以 SSE 流式方式回复并追问，用户可实时看到 AI 正在输入。
5. 右侧文档区：随 SSE 事件实时更新当前 spec / tasks / checklist 草案。
6. AI 判断信息足够后，在聊天区提示并启用「创建任务」按钮。
7. 用户可点击顶部「历史会话」入口，查看/继续/删除之前的会话。
8. 用户点击「创建任务」，可选择保存当前会话（默认保存），三件套保存到任务记录，返回任务队列。
9. 在任务队列中，用户手动选择子系统（如 programming agent）执行。
10. 中枢将三件套推送给对应子系统，子系统返回固定响应。
```

### 4.2 继续历史会话流程

```text
1. 用户在 /tasks/new 页面点击「历史会话」入口，打开会话列表。
2. 用户选择一条历史会话，页面加载该会话的 messages 和 specification。
3. 用户继续对话，所有变更自动保存到该会话记录。
4. 用户点击「创建任务」，任务从该会话创建，并保留 session_id 关联。
```

## 5. 页面设计

### 5.1 入口

- 左侧边栏新增「新建任务」菜单项，路由为 `/tasks/new`。
- 原仪表盘上的「+ 新建任务」按钮可保留，点击后也进入 `/tasks/new`。
- `/tasks/new` 页面内提供「历史会话」入口（按钮或抽屉），点击后展示历史会话列表。

### 5.2 布局

`/tasks/new` 采用左右分栏（2026-09-13 布局改版 + 同日二次纠偏，ADR-010 模块 UI 迭代）：

- 顶部：**无固定工具栏**。仅在迭代模式（URL 携带 `parent`）时显示迭代上下文条
  （父任务标识 + 上一轮修改说明折叠 + 本轮修改说明输入）。
- 左侧（主区）：聊天区
  - 顶部右侧：显眼「历史会话」按钮（图标 + 文字，打开会话抽屉）。
  - 中部：对话历史列表（用户消息 + AI 回复，AI 回复以打字机效果流式渲染）。
  - 底部：输入卡片（TextArea），卡片底栏一行紧凑控件（参考主流 AI 对话框样式）：
    - 左侧依次：对话模型下拉（热切换，ADR-030）、模式下拉（合并「快速/思考」
      与「直接开发/先规划」两组切换）、子 Agent 执行模型选择（ADR-029/030）。
    - 右侧：字数计数 + 发送按钮（交互不变：发送/「回答中」两态）。
- 右侧 = **工具面板**（默认 400px，可拖拽 320~900，可折叠为悬浮按钮）：
  - 顶部常驻：工作目录行——文件夹图标按钮（点击打开系统目录选择，仅桌面壳；
    浏览器环境点击提示）+ 当前已选目录展示。**不再支持手动输入路径**，
    也不再提供历史目录下拉（原 AutoComplete 移除）。
  - **空态**（未打开任何工具）：「从这里开始」+ 工具入口列表，用户选择打开：
    任务摘要（执行进度/产物汇总）、产物（浏览和预览 Agent 交付的文件）、
    规范文档、任务清单、验收清单、方案（审批跟踪）。
  - **已打开**：顶部标签页（激活高亮 + 单独关闭），标签栏「+」下拉可继续打开
    未开的工具；内容区显示激活工具视图（任务摘要为只读卡片，无跟踪任务时
    显示占位提示；产物为文件树 + 单文件预览 + 打包下载，复用任务详情产物
    资源管理器 panel 形态；文档类工具含编辑器与「保存会话/创建任务」操作栏）。
  - 自动打开规则：URL 携带 `?task=` 进入跟踪态、任务创建成功、任务队列
    「继续修改」入口 → 自动打开「方案」标签；继续历史会话 → 自动打开「规范文档」。

历史会话按当前工作目录过滤（ADR-028）逻辑保留：目录有值时抽屉内仅展示同目录任务会话。

进入页面时，右侧默认显示欢迎语或空模板：

```markdown
# 任务规范

在左侧输入你的需求，AI 会帮你逐步完善这份规范。
```

### 5.3 历史会话抽屉

- 从 `/tasks/new` 页面右侧滑出抽屉，展示历史会话列表。
- 每条会话显示：标题、最后更新时间、模型/模式标签。
- 操作：继续（加载会话到当前页面）、删除（二次确认）。
- 提供「新建会话」按钮，清空当前页面状态。

## 6. 数据模型

### 6.1 tasks 表扩展

复用现有 `tasks` 表，新增字段（数据库为 PostgreSQL）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `specification` | JSONB | 保存三件套 `{ "spec": "...", "tasks": "...", "checklist": "..." }` |
| `source` | VARCHAR(32) | 任务来源，默认 `"manual"`，Spec Chat 创建时为 `"spec_chat"` |
| `execution_target` | VARCHAR(32) / NULL | MVP 阶段用户手动指定的子系统标识 |
| `session_id` | UUID / NULL | 关联的 task_building_sessions 会话 ID |

### 6.2 task_building_sessions 表（新增）

用于持久化 Spec Chat 历史会话：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 主键 |
| `title` | VARCHAR(255) | 会话标题，取首条用户消息前 50 字 |
| `messages` | JSONB | 对话历史 `[{ "role": "user", "content": "..." }, ...]` |
| `specification` | JSONB | 当前三件套 `{ "spec": "...", "tasks": "...", "checklist": "..." }` |
| `mode` | VARCHAR(16) | `fast` 或 `thinking` |
| `model` | VARCHAR(64) | 当前模型标识，如 `deepseek-chat` |
| `created_at` | TIMESTAMP | 创建时间 |
| `updated_at` | TIMESTAMP | 最后更新时间 |

MVP 阶段无用户体系，`task_building_sessions` 全局可见；后续接入用户体系后新增 `user_id` 字段并建立索引。

## 7. API 设计

### 7.1 同步对话接口（保留，向后兼容）

`POST /api/v1/tasks/build/chat`

请求体：

```json
{
  "messages": [
    { "role": "user", "content": "我要开发一个用户登录接口" },
    { "role": "assistant", "content": "好的，请补充技术栈..." }
  ],
  "current_specification": {
    "spec": "# Spec\n...",
    "tasks": "# Tasks\n...",
    "checklist": "# Checklist\n..."
  },
  "mode": "thinking"
}
```

响应：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "ai_reply": "了解了，还需要确认一下...",
    "specification": {
      "spec": "# Spec\n...",
      "tasks": "# Tasks\n...",
      "checklist": "# Checklist\n..."
    },
    "is_ready_to_create": false,
    "model": "deepseek-chat",
    "mode": "thinking"
  }
}
```

### 7.2 SSE 流式对话接口（MVP 主路径）

`POST /api/v1/tasks/build/chat/stream`

请求头：`Accept: text/event-stream`

请求体与 7.1 相同，响应为 SSE 事件流：

```text
id: 1
event: meta
data: {"model":"deepseek-chat","mode":"thinking"}

id: 2
event: chunk
data: {"delta":"了解了"}

id: 3
event: chunk
data: {"delta":"，还需要确认一下..."}

id: 4
event: specification
data: {"spec":"# Spec\n...","tasks":"# Tasks\n...","checklist":"# Checklist\n..."}

id: 5
event: status
data: {"is_ready_to_create":false}

id: 6
event: done
data: {}
```

事件类型说明：

| 事件 | 说明 |
|---|---|
| `meta` | 首事件，返回 `model` 和 `mode`，用于前端显示 |
| `chunk` | AI 回复文本增量，前端以打字机效果追加 |
| `specification` | 三件套完整更新（AI 判断信息足够或发生显著变化时推送） |
| `status` | `is_ready_to_create` 状态变更 |
| `error` | 生成异常，前端停止渲染并提示用户 |
| `done` | 本次流式响应结束 |

### 7.3 创建任务接口（复用并扩展）

`POST /api/v1/tasks`

请求体扩展：

```json
{
  "requirement": "我要开发一个用户登录接口",
  "specification": {
    "spec": "...",
    "tasks": "...",
    "checklist": "..."
  },
  "session_id": "0191b0c0-..."
}
```

`TaskService.create_task` 内部：

- 如果请求包含 `specification`，直接保存（不再调用 Planning Agent），`source="spec_chat"`。
- 如果同时包含 `session_id`，任务记录关联该会话。
- 如果不包含 `specification`，保持原有行为：调用 Planning Agent 生成任务。

### 7.4 历史会话接口

#### 7.4.1 创建会话

`POST /api/v1/task-building-sessions`

请求体：

```json
{
  "title": "开发用户登录接口",
  "messages": [...],
  "specification": { "spec": "...", "tasks": "...", "checklist": "..." },
  "mode": "thinking"
}
```

响应返回会话完整对象，包含 `id`、`created_at`、`updated_at`、`model`。

#### 7.4.2 获取会话列表

`GET /api/v1/task-building-sessions?limit=20&offset=0`

响应：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "items": [...],
    "total": 100
  }
}
```

#### 7.4.3 获取单个会话

`GET /api/v1/task-building-sessions/{session_id}`

#### 7.4.4 更新会话

`PATCH /api/v1/task-building-sessions/{session_id}`

请求体可部分更新 `title`、`messages`、`specification`、`mode`。

#### 7.4.5 删除会话

`DELETE /api/v1/task-building-sessions/{session_id}`

### 7.5 手动派发接口（MVP）

`POST /api/v1/tasks/{task_id}/dispatch`

请求体：

```json
{
  "target": "programming"
}
```

行为：

- 校验任务存在且包含 `specification`。
- 将 `specification` 通过 HTTP 推送给目标子系统（MVP 为中枢 mock 接口）。
- 更新 `execution_target` 字段。
- 返回推送结果。

### 7.6 子系统接收接口（MVP mock）

`POST /api/v1/subsystems/{subsystem_id}/accept-task`

MVP 阶段该接口放在中枢后端实现，用于模拟子系统接收任务：

- 校验请求中的 `task_id` 和 `specification`。
- 仅做参数校验和日志记录。
- 返回固定响应，不真正转发给独立 Agent 节点。

待各 Agent 子系统开发完毕后，该接口迁移至各 Agent 节点暴露真实端点，中枢改为正向转发请求。

固定响应：

```json
{
  "status": "accepted",
  "task_id": "..."
}
```

## 8. Spec Agent 设计

新增 `app/agents/spec_builder.py`：

### 8.1 职责

- 接收完整对话历史 + 当前三件套草案 + 模式（`fast` / `thinking`）。
- 判断信息是否足够。
- 不够：生成追问问题。
- 够：生成/更新 `spec.md`、`tasks.md`、`checklist.md`。
- 同步接口返回完整 JSON；流式接口通过 AsyncGenerator 逐 chunk 输出，最终组装完整结构化结果。

### 8.2 输出格式

```json
{
  "ai_reply": "string",
  "specification": {
    "spec": "string (markdown)",
    "tasks": "string (markdown)",
    "checklist": "string (markdown)"
  },
  "is_ready_to_create": true | false
}
```

### 8.3 Prompt 核心要求

- 明确告诉 AI 它的角色是「需求澄清助手」。
- 要求 AI 在信息不足时主动追问，每次最多追问 1-3 个问题。
- 信息足够时生成三件套，内容用 Markdown。
- `spec.md` 包含：Why、What Changes、Impact、Requirements。
- `tasks.md` 包含：有序任务清单。
- `checklist.md` 包含：验收检查点。
- 必须输出合法 JSON，Markdown 内容转义。

### 8.4 模式切换

- `mode=fast`：关闭深度思考，使用精简 system prompt，要求 AI 简短追问、快速收敛。
- `mode=thinking`：开启深度思考（通过 DeepSeek `reasoning_effort` 或 `thinking` 参数），使用完整 system prompt，允许 AI 进行更详细的推理和追问。
- 单模型实现，无需切换模型，通过调用参数和 prompt 差异控制。

### 8.5 流式输出

- `chat_stream` 方法返回 `AsyncGenerator[SpecBuilderStreamEvent, None]`。
- 首事件为 `meta`，包含 `model` 和 `mode`。
- 中间事件为 `chunk`，增量文本来自 LLM 流式响应。
- 当流式响应结束时，服务端解析完整 JSON：
  - 如果解析成功，发送 `specification` 事件和 `status` 事件。
  - 如果解析失败，发送 `error` 事件。
- 最后发送 `done` 事件关闭流。
- 同步接口 `chat` 复用同一 prompt 和解析逻辑，但一次性返回完整结果。

## 9. MVP 子系统交互

### 9.1 任务队列增强

- 每个来源为 `spec_chat` 的任务在任务队列中显示「选择子系统执行」下拉框。
- 下拉框选项初期固定：`programming`。
- 用户选择后调用 `/api/v1/tasks/{task_id}/dispatch`。

### 9.2 推送内容

推送 payload：

```json
{
  "event": "agent.task_dispatch",
  "payload": {
    "task_id": "...",
    "specification": {
      "spec": "...",
      "tasks": "...",
      "checklist": "..."
    }
  }
}
```

### 9.3 子系统响应

MVP 阶段子系统收到后返回：

```json
{
  "status": "accepted",
  "task_id": "..."
}
```

## 10. 错误处理

- Spec Agent 输出非法 JSON：返回 500，记录异常，前端提示「AI 生成规范失败，请重试」。
- 创建任务时缺少 `specification` 但来源为 `spec_chat`：视为参数错误。
- 派发时子系统离线：记录日志，任务状态保持「已分配」，提示用户子系统不可用。

## 11. 验收标准

### 11.1 基础功能（已验证）

- [x] 侧边栏新增「新建任务」入口，点击进入 `/tasks/new`。
- [x] `/tasks/new` 页面左右分栏，左侧聊天、右侧三件套文档。
- [x] 用户输入需求后，AI 能主动追问缺失信息。
- [x] 信息足够后，AI 生成并更新 spec / tasks / checklist。
- [x] 用户可以编辑右侧 Markdown 文档。
- [x] AI 判断信息足够后启用「创建任务」按钮。
- [x] 点击「创建任务」后，任务进入队列且包含三件套。
- [x] 任务队列中来源为 `spec_chat` 的任务可手动选择子系统执行。
- [x] 派发后子系统能收到三件套并返回固定响应。
- [x] `pytest` 全部通过，`ruff check` 无错误。

### 11.2 本次新增功能

- [ ] `POST /api/v1/tasks/build/chat/stream` SSE 接口可用，前端能逐字接收 AI 回复。
- [ ] `/tasks/new` 顶部显示当前模型标识（如 `deepseek-chat`）。
- [ ] `/tasks/new` 顶部提供 `fast` / `thinking` 模式切换，切换后新对话使用该模式。
- [ ] `thinking` 模式通过 DeepSeek 推理参数开启深度思考，`fast` 模式关闭。
- [ ] 新增 `task_building_sessions` 表，历史会话可持久化。
- [ ] `/tasks/new` 提供历史会话入口，支持继续会话、删除会话、新建会话。
- [ ] 创建任务时可选择保存当前会话（默认保存）。
- [ ] 前端引入 Vitest + RTL + Playwright + MSW 测试框架。
- [ ] 新增至少 1 个前端组件测试和 1 个 Playwright E2E 测试。
- [ ] 后端新增 SSE 接口测试、历史会话 CRUD 测试、思考模式参数测试。
