# OPC 项目 API 接口规范

## 1. 通信架构

- **中央调度中枢 ↔ 前端控制台**：REST API + WebSocket（用于实时进度推送）。
- **中央调度中枢 ↔ Agent 节点**：REST API（任务下发/查询）+ WebSocket（心跳、进度上报、结果回传）。
- **节点之间禁止直接通信**，大文件统一走共享存储，接口只传路径和元数据。

## 2. REST API 基础规范

### 2.1 URL 设计

- 使用名词复数，不用动词。
- 层级关系用 `/` 表达。
- 全部小写，连字符分隔。
- **动作端点豁免**：复杂动作（派发、批准、检查、导入等）允许 RPC 风格 `POST /api/v1/资源/{id}/动词短语`（kebab-case），如 `POST /tasks/{id}/approve-plan`、`POST /settings/resources/check`。动作端点必须使用 POST。

```
GET    /api/v1/tasks              # 任务列表
POST   /api/v1/tasks              # 创建任务
GET    /api/v1/tasks/{task_id}    # 任务详情
PATCH  /api/v1/tasks/{task_id}    # 更新任务
DELETE /api/v1/tasks/{task_id}    # 删除任务

GET    /api/v1/agent-nodes        # 节点列表
POST   /api/v1/agent-nodes/{id}/token      # 重置节点 Token（动作端点）
```

### 2.2 HTTP 方法

| 方法 | 用途 |
|------|------|
| GET | 获取资源 |
| POST | 创建资源 / 执行动作 |
| PATCH | 部分更新 |
| PUT | 全量更新（少用） |
| DELETE | 删除资源 |

### 2.3 统一响应格式

```json
{
  "code": 0,
  "message": "success",
  "data": { ... },
  "request_id": "req_xxxxxxxx"
}
```

| 字段 | 说明 |
|------|------|
| `code` | 业务状态码，`0` 表示成功，非 `0` 表示业务错误 |
| `message` | 可读提示信息 |
| `data` | 实际返回数据，可为 null / 对象 / 数组 |
| `request_id` | 每次请求唯一 ID，便于链路追踪 |

**失败语义**：

- 主动操作类接口（创建、更新、删除、派发、同步等）任何未达成功的场景必须返回非 `0` 业务码，禁止在 `code=0` 的响应中用 `data.status="failed"` 表达失败。
- 查询展示类接口在外部依赖不可达时允许 `code=0` 降级，失败原因放 `data.error` 字段（前端按降级展示）。

### 2.4 分页格式

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [ ... ],
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total": 100,
      "total_pages": 5
    }
  }
}
```

### 2.5 HTTP 状态码

| 状态码 | 使用场景 |
|--------|---------|
| 200 OK | 成功 |
| 201 Created | 资源创建成功 |
| 204 No Content | 删除成功或无返回体 |
| 400 Bad Request | 请求参数错误 |
| 401 Unauthorized | 未认证 |
| 403 Forbidden | 无权限 |
| 404 Not Found | 资源不存在 |
| 409 Conflict | 资源冲突（如重复创建） |
| 422 Unprocessable Entity | 业务校验失败 |
| 429 Too Many Requests | 限流 |
| 500 Internal Server Error | 服务器内部错误 |

## 3. 业务错误码规范

错误码采用 `A-BB-CCC` 格式：

| 段 | 含义 | 示例 |
|----|------|------|
| A | 服务模块（1=通用，2=中枢，3=Agent，4=文件存储，5=用户系统） | `2` |
| BB | 功能域 | `01=任务调度`，`02=节点管理`，`03=系统配置（中枢）`，`01=文件资源（文件存储域）` |
| CCC | 具体错误 | `001=任务不存在` |

示例：

| 错误码 | 含义 |
|--------|------|
| `100001` | 参数校验失败 |
| `100002` | 请求频率过高 |
| `100003` | 服务器内部错误（通用，不挂在具体业务域） |
| `100004` | 资源不存在（无法归类到具体业务域时的 404 兜底） |
| `201001` | 任务不存在 |
| `201002` | 任务状态不允许此操作 |
| `201003` | 无可用 Agent 节点 |
| `201004` | 任务构建会话不存在 |
| `201006` | 任务规划（AI Planning）失败 |
| `202001` | Agent 节点不存在（未注册） |
| `202002` | Agent 节点离线 |
| `202003` | 心跳超时 |
| `202004` | 安装脚本不支持（Agent 类型/操作系统无对应模板） |
| `202010` | 节点名称重复（ADR-016） |
| `202011` | 节点名称非法（ADR-016） |
| `203001` | LLM 配置不存在 |
| `204001` | 技能不存在（ADR-033） |
| `205001` | 附件类型/大小校验失败（ADR-039） |
| `205002` | 附件不存在（ADR-039） |
| `301001` | Agent 任务执行失败 |
| `301002` | Agent 交付物上传失败 |
| `401001` | 文件不存在 |
| `401002` | 文件上传失败 |
| `401003` | 资源同步/导入失败（sha256 校验失败、manifest 非法、云端不可达等） |

## 4. WebSocket 规范

### 4.1 消息格式

```json
{
  "event": "task_progress",
  "payload": { ... },
  "timestamp": "2026-08-03T12:00:00Z",
  "request_id": "req_xxxxxxxx"
}
```

### 4.2 核心事件

#### 中枢 → 前端

| event | 说明 |
|-------|------|
| `task.created` | 任务已创建 |
| `task.assigned` | 任务已分配 |
| `task.progress` | 任务进度更新 |
| `task.completed` | 任务完成 |
| `task.failed` | 任务失败 |
| `agent.status_changed` | 节点状态变化 |
| `system.alert` | 系统告警 |

#### Agent → 中枢

| event | 说明 |
|-------|------|
| `agent.heartbeat` | 心跳上报（含 status：starting/idle/busy、current_task_id） |
| `agent.task_accepted` | 任务已接受（assigned → running） |
| `agent.task_rejected` | 任务拒绝（回 pending，释放节点） |
| `agent.task_started` | 任务开始执行 |
| `agent.task_progress` | 任务进度上报（payload 含 task_id、progress int 0-100；ADR-055 扩展 `stage` 执行阶段封闭枚举、`message` 当前动作描述 ≤120 字符、`tool` 当前工具名、`detail` 可观测附加信息 {files, tokens, cost, compaction}——新增键向后兼容，旧中枢忽略） |
| `agent.task_completed` | 任务完成（payload 可含 session_id、plan_content、change_summary、project_context——AI 生成的项目上下文记忆，ADR-034） |
| `agent.task_failed` | 任务失败（payload 含 task_id、reason；assigned/running → failed，释放节点） |
| `agent.task_rejected` | 任务拒绝（payload 含 `task_id`、`reason`）；ADR-059 起 `reason` **落库到任务 `last_reject_reason` 并对用户可见**（前端展示「节点 X 因 <原因> 拒单，已回避 5 分钟」）。取值域：`slots_full`（并发槽满）/`resource_busy`（本地自评不可自主）/`bridge_not_ready`（执行层未就绪）/`shutting_down`（节点关闭） |

#### 中枢 → Agent

| event | 说明 |
|-------|------|
| `hub.task_dispatch` | 任务下发（payload 含 task_id、task_name、description、mode: plan\|build、specification 三件套、executor_model；链上有历史现场时可含 handoff: {bundle_path, sha256, size, context_md}——现场迁移，ADR-034；任务带附件时可含 attachments: [{id, file_name, mime, size, url}]——url 为一次性 token 下载相对路径，无附件缺省，ADR-039） |
| `hub.task_cancel` | 任务取消指令（payload 含 task_id、reason；节点重连对账发现执行中任务已改派/已终态时推送，节点本地中断执行，见 ADR-020） |

### 4.3 SSE 流式接口

流式对话类端点（如 `POST /tasks/build/chat/stream`）返回 `text/event-stream`，不使用统一响应格式包装：

```
event: chunk
data: {"delta": "..."}

event: step_started
data: {"step_id": "uuid", "seq": 1, "skill_name": "find_idle_nodes", "skill_display_name": "查找空闲节点", "args_summary": "{\"agent_type\": \"code\"}"}

event: step_finished
data: {"step_id": "uuid", "seq": 1, "status": "success|failed", "result_summary": "...", "duration_ms": 452}

event: specification_delta
data: {"field": "spec|tasks|checklist", "delta": "..."}

event: specification
data: { ... }

event: status
data: {"is_ready_to_create": true, "message": "正在编写规范文档…"}
```

- 事件名为小写 snake_case，与 WS 事件命名风格一致。
- `step_started` / `step_finished` 为技能执行步骤事件（ADR-033 执行链路可视化）：`seq` 为本轮对话内技能调用序号，前端渲染可折叠步骤卡片。
- `specification_delta` 为三件套流式增量（field + delta，ADR-028 UI 增强），随后仍发全量 `specification` 做最终校准。
- `status` 事件的 `message` 为可选阶段提示（如「正在编写规范文档…」），`is_ready_to_create` 为可选布尔。
- request_id 由中间件注入到响应头/首个事件，便于链路追踪。
- 中间件对 `text/event-stream` 响应豁免 request_id 响应体注入，避免破坏流式分块。

## 5. 请求/响应示例

### 创建任务

**Request:**

```http
POST /api/v1/tasks
Content-Type: application/json

{
  "title": "开发企业官网",
  "description": "使用 React + FastAPI 开发一个企业官网",
  "requirements": ["响应式布局", "SEO 优化"],
  "priority": "high",
  "expected_deliverables": ["源代码", "部署文档"]
}
```

**Response:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "task_xxxxxxxx",
    "status": "pending",
    "created_at": "2026-08-03T12:00:00Z"
  },
  "request_id": "req_xxxxxxxx"
}
```

## 6. 安全规范

- 所有 API 必须 HTTPS/WSS 传输。
- Agent 节点注册必须携带预共享 Token 或证书。
- 敏感操作（高风险任务确认）需要人工审批 Token。
- 对 API 进行限流，防止节点异常上报压垮中枢。

## 7. 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-08-03 | 初始创建 | AI助手 |
| 2026-08-15 | §4.2 事件表按中枢实际实现修正：移除未实现的 `agent.task_result`/`agent.task_log`/`agent.error`/`agent.register`，补充 `agent.task_accepted`/`rejected`/`started`/`failed` 与 `hub.task_dispatch`（含 mode/specification）；与 ADR-012 tilacode 执行桥接实现同步 | AI助手 |
| 2026-09-03 | §4.2 中枢 → Agent 事件表新增 `hub.task_cancel`（重连任务对账取消指令）；与 ADR-020 断线重连与任务对账机制同步 | AI助手 |
| 2026-09-03 | §3 错误码表补充 `201006`（任务规划失败）；修正代码实现与规范错误码语义冲突（原实现中 201002/201003 被错误占用，已改为 201006/100001 对齐本规范） | AI助手 |
| 2026-09-09 | API 规范审计修复：① §3 错误码对齐代码实现——补充 `100003`（服务器内部错误，原误占 201004）、`100004`（404 通用兜底）、`201004`（任务构建会话不存在）、`202004`（安装脚本不支持，原误占 202002 节点离线）、`202010/202011`（节点名称，ADR-016）、`203001`（LLM 配置）、`401003`（资源同步失败）；`202001` 语义修正为"节点不存在（未注册）"；② §2.1 新增动作端点豁免条款，心跳示例移除（心跳已走 WS `agent.heartbeat`）；③ §2.3 新增失败语义条款（主动操作失败必须非 0 码；查询降级允许 code=0 + data.error）；④ 新增 §4.3 SSE 流式接口约定 | AI助手 |
| 2026-09-15 | ADR-033 登记：① §3 错误码新增 `204001`（技能不存在）；② §4.3 SSE 事件新增 `step_started`/`step_finished`（技能执行步骤，执行链路可视化）；③ 新增技能域 REST 端点（GET/GET/PATCH `/skills`、GET/PUT `/settings/ai-dispatch`） | AI助手 |
| 2026-09-15 | ADR-034 登记：① §4.2 `agent.task_completed` payload 扩展 `project_context`；`hub.task_dispatch` payload 可含 `handoff`（现场迁移）；② 新增端点 `GET /tasks/{task_id}/handoff-bundle?token=`（一次性下载，Agent 内部）与 `GET /tasks/{task_id}/context`（链上下文查询） | AI助手 |
| 2026-09-21 | ADR-039 登记：① §3 错误码新增 `205001`（附件校验失败）、`205002`（附件不存在）；② 新增附件域端点——`POST/GET /sessions/{session_id}/attachments`（multipart 上传/列表）、`GET /sessions/{session_id}/attachments/{attachment_id}/content`（原图预览）、`GET /tasks/{task_id}/attachments/{attachment_id}?token=`（一次性下载，Agent 内部）；③ §4.2 `hub.task_dispatch` payload 可含 `attachments`；④ 对话流新增 `warning` SSE 事件（非致命提示，如对话模型不支持图片） | AI助手 |
| 2026-09-27 | ADR-055 登记：§4.2 `agent.task_progress` payload 扩展 `stage`（preparing/planning/coding/editing/running_command/searching/testing/compacting/summarizing/idle 封闭枚举）、`message`（当前动作描述，≤120 字符）、`tool`（当前工具名）、`detail`（{files, tokens, cost, compaction}）；新增键向后兼容，旧中枢按 `extra=ignore` 忽略。同批：`hub.task_dispatch` payload 可含可选 `timeout_seconds`（任务总时长上限，对齐 agent-node-standards §5.3；缺省 0=不限，节点另有无进度看门狗） | AI助手 |
| 2026-09-28 | ADR-059 登记（**已按实际实现校正**）：① §4.2 明确 `agent.task_rejected` 的 `reason` 取值域，并规定**必须落库可见**（写入 `tasks.last_reject_reason`，不再仅记日志）；② `agent.task_accepted`/`agent.task_started` 语义补充：受理成功后中枢**清空** `last_reject_reason` 与 `dispatch_hold_reason`；③ 新增任务端点 `PUT /tasks/{task_id}/dispatch-preference`（body `{target_node_id, force}`——指定节点排队 / 唯一越门槛开关 `force`，落库 `target_node_id`、`forced_dispatch`）与 `GET /tasks/{task_id}/eligibility`（返回各候选节点 `available_memory_gb`/`load_score`/`slots_free`/`eligible`/`blocking` 与下一步建议）；④ `TaskResponse` 新增 `target_node_id`/`target_node_name`/`last_reject_reason`/`dispatch_hold_reason`/`forced_dispatch` 字段；⑤ 产物同步说明：执行中由中枢按 `agent.file_tree` 的 size/mtime 差集**增量拉取**变更文件（复用 `hub.file_fetch` + `agent.file_chunk`），**不新增协议事件**。**更正原计划**：曾拟新增「中枢 → 前端对话事件 `dispatch_hold`」，实际**未实现**（中枢无任务级事件总线），排队信息改由 `dispatch_hold_reason` 字段 + `GET /tasks/{task_id}/eligibility` 提供 | AI助手 |
