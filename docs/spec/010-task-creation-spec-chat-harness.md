# 任务创建 Spec Chat Harness

## 1. 测试目标

验证任务创建 Spec Chat 模块的多轮对话、三件套生成与解析、SSE 流式输出、模型显示、思考/快速模式、历史会话持久化、任务创建扩展、手动派发与 mock 子系统接收等核心功能，确保：

- SpecBuilderAgent 对 DeepSeek 输出的各类异常格式（Markdown 代码块、字段平铺、非法 JSON、字段缺失）能正确兼容或抛出业务异常。
- `/tasks/build/chat`、`/tasks/build/chat/stream`、`/task-building-sessions`、`/tasks`、`/tasks/{id}/dispatch`、`/subsystems/{id}/accept-task` 接口遵循 `api-standards.md` 统一响应格式。
- SSE 流式接口事件顺序正确（`meta` → `chunk` ... → `specification`/`status` → `done`）。
- 数据库迁移正确新增 `specification`、`source`、`execution_target`、`session_id` 字段，并新增 `task_building_sessions` 表。
- 历史会话 CRUD 接口在事务内隔离，不污染数据。
- 思考模式正确传递 `reasoning_effort`/`thinking` 参数到 DeepSeekClient。
- 任务派发链路在 mock 模式下端到端闭环，缺规范的派发能正确抛出 `TaskDispatchError`。
- AI 调用相关测试全部使用 mock，不消耗真实 token。

## 2. 测试金字塔

```text
        /\
       /  \   E2E：前端 /tasks/new SSE 对话 → 三件套生成 → 历史会话 → 创建任务 → 任务队列手动派发 → mock 子系统接收
      /____\
     /      \  后端集成：/tasks/build/chat/stream + SpecBuilderService + DeepSeekClient(mock) + 数据库
    /________\
   /          \  后端单元：SpecBuilderAgent 解析/校验/重试/流式、Schema 校验、TaskService.dispatch_task
  /____________\
   /          \   前端集成：Vitest + RTL + MSW 组件/API 测试
  /____________\
   /          \   前端单元：TaskNew 状态逻辑、SSE 事件处理工具函数
  /____________\
```

## 3. 单元测试清单

### 3.1 SpecBuilderAgent 解析与校验（`tests/agents/test_spec_builder.py`）

| 测试项 | 说明 |
|--------|------|
| `_parse_response` 解析普通 JSON | 验证顶层三字段与 specification 嵌套结构正确解析 |
| `_parse_response` 解析 Markdown 代码块包裹的 JSON | 验证 ```` ```json ```` 包裹内容能被剥离后解析 |
| `_parse_response` 平铺字段自动组装 | 验证模型把 `spec/tasks/checklist` 平铺顶层时 `_normalize_flat_fields` 组装回 `specification` |
| `_parse_response` 非法 JSON 抛出 `TaskSpecificationError` | 验证非 JSON 文本抛出含「格式错误」的异常 |
| `_validate_parsed` 缺少 specification 字段抛异常 | 验证异常信息包含 `specification` |
| `_validate_parsed` specification.spec 类型错误抛异常 | 验证 `spec` 非字符串时抛出含 `spec` 的异常 |
| `chat` 首次输出非 JSON 时自动重试并成功 | 验证 `MAX_PARSE_RETRIES=1` 重试机制，第二次成功后返回结果，`await_count == 2` |
| `chat` 重试后仍无法解析时抛出异常 | 验证重试耗尽后抛出 `TaskSpecificationError`，`await_count == 2` |
| `chat_stream` 正确产出 meta/chunk/specification/status/done 事件 | 验证流式事件顺序和字段 |
| `chat_stream` 解析失败时发送 error + done 事件 | 验证流式错误处理不抛异常，以 SSE error 事件结束 |
| `_build_messages` fast 模式使用精简 prompt | 验证 `mode=fast` 时 system prompt 长度小于 thinking 模式 |
| `_build_messages` thinking 模式追加 reasoning 提示 | 验证 `mode=thinking` 时 prompt 包含推理要求 |

### 3.2 Schema 校验（`app/schemas/task_building.py`）

| 测试项 | 说明 |
|--------|------|
| `TaskBuildChatRequest.messages` 为空时 422 | 验证 `min_length=1` 约束 |
| `ChatMessage.role` 非 `user`/`assistant` 时 422 | 验证 `pattern` 约束 |
| `TaskBuildChatRequest.mode` 非 `fast`/`thinking` 时 422 | 验证枚举约束 |
| `TaskSpecification` 三字段必填 | 验证缺 `spec`/`tasks`/`checklist` 任一字段返回 422 |
| `TaskCreateRequest.session_id` 非法 UUID 时 422 | 验证 UUID 格式 |
| `TaskBuildingSessionCreate.title` 超长时 422 | 验证 `max_length=255` 约束 |

## 4. 集成测试清单

### 4.1 任务构建对话接口（`tests/api/v1/test_task_building.py`）

| 测试项 | 说明 |
|--------|------|
| `POST /tasks/build/chat` 成功返回三件套结构 | Mock `SpecBuilderService.chat`，验证 `code=0`、`ai_reply`、`specification.spec`、`is_ready_to_create=False`、`model`、`mode` |
| 携带 `current_specification` 时接口正确转发 | 验证请求体含 `current_specification` 时返回 `is_ready_to_create=True` |
| `POST /tasks/build/chat/stream` 返回 SSE 事件流 | Mock `SpecBuilderService.chat_stream`，使用 `TestClient` 读取 event stream，验证事件顺序 `meta` → `chunk` → `specification` → `status` → `done` |
| SSE 接口 `mode=thinking` 时返回对应模式 | 验证 `meta` 事件中 `mode=thinking` |
| SSE 接口流式过程中发生错误返回 error 事件 | Mock `SpecBuilderService.chat_stream` 产出 `error` 事件，验证客户端收到并正常关闭 |

### 4.2 历史会话接口（`tests/api/v1/test_task_building_sessions.py`）

| 测试项 | 说明 |
|--------|------|
| `POST /task-building-sessions` 创建会话成功 | 验证返回对象包含 `id`、`title`、`messages`、`specification`、`mode`、`model`、`created_at`、`updated_at` |
| `GET /task-building-sessions` 分页列表 | 验证 `items` 和 `total` 字段 |
| `GET /task-building-sessions/{id}` 读取单个会话 | 验证返回完整会话 |
| `PATCH /task-building-sessions/{id}` 更新标题和消息 | 验证 `updated_at` 更新 |
| `DELETE /task-building-sessions/{id}` 删除会话 | 验证删除后 404 |
| 删除不存在会话返回 404 | 验证错误处理 |

### 4.3 任务派发与 mock 接收接口（`tests/api/v1/test_task_dispatch.py`）

| 测试项 | 说明 |
|--------|------|
| `accept_task` 函数级 mock 返回 `accepted` | 验证 `AcceptTaskResponse.status == "accepted"`、`task_id` 回显 |
| `POST /subsystems/{id}/accept-task` HTTP 层返回 `accepted` | 验证 HTTP 状态码 200、`code=0`、`data.status` |
| `dispatch_task` 成功更新 `execution_target` 和状态 | Mock `httpx.AsyncClient`，验证 `status=dispatched`、`task.execution_target=programming`、`task.status=assigned` |
| `dispatch_task` 缺 `specification` 抛出 `TaskDispatchError` | 验证异常信息含「缺少规范」 |

### 4.4 任务创建接口扩展（`tests/api/v1/test_tasks.py`）

| 测试项 | 说明 |
|--------|------|
| `POST /tasks` 携带 `specification` 时 `source="spec_chat"` 且不调用 Planning Agent | 验证 `specification` 正确落库、`source` 字段值 |
| `POST /tasks` 携带 `specification` 和 `session_id` 时关联会话 | 验证 `task.session_id` 正确落库 |
| `POST /tasks` 不携带 `specification` 时保持原有 Planning Agent 行为 | 验证向后兼容，不引入回归 |

## 5. E2E 测试场景

### 场景 1：用户通过 SSE 多轮对话生成三件套并创建任务

1. 用户进入 `/tasks/new`，页面顶部显示当前模型（如 `deepseek-chat`）和 `fast/thinking` 模式切换。
2. 用户切换为 `thinking` 模式，输入「开发一个登录接口」。
3. 前端通过 `EventSource` 连接 `POST /tasks/build/chat/stream`，首事件 `meta` 返回 `model=deepseek-chat`、`mode=thinking`。
4. AI 以 `chunk` 事件流式回复追问，前端打字机效果展示。
5. 用户补充「FastAPI + PostgreSQL」，再次发起 SSE 请求。
6. AI 判断信息足够，发送 `specification` 事件更新右侧三件套，发送 `status` 事件 `is_ready_to_create=true`，最后 `done` 事件关闭。
7. 用户编辑右侧 `spec.md` 后点击「创建任务」。
8. 前端调用 `POST /tasks`（含 `specification`），任务落库 `source=spec_chat`。
9. 任务出现在任务队列，状态 `pending`。

### 场景 2：任务队列手动派发到 programming 子系统（MVP mock）

1. 在任务队列中选中场景 1 创建的任务。
2. 选择子系统 `programming`，前端调用 `POST /tasks/{id}/dispatch`。
3. 后端 `TaskService.dispatch_task` 通过 httpx 调用 `POST /api/v1/subsystems/programming/accept-task` mock 接口。
4. mock 接口返回 `{"status":"accepted"}`。
5. 后端更新 `task.execution_target=programming`、`task.status=assigned`。
6. 前端刷新任务队列，任务状态变为 `assigned`。

### 场景 3：DeepSeek 输出异常格式时的容错

1. DeepSeek 返回 Markdown 代码块包裹的 JSON。
2. `_parse_response` 剥离代码块后解析成功。
3. DeepSeek 返回字段平铺的 JSON（`specification` 为空字符串，`spec/tasks/checklist` 在顶层）。
4. `_normalize_flat_fields` 自动组装回嵌套结构。
5. DeepSeek 首次返回非 JSON 文本，触发 `RETRY_PROMPT` 重试。
6. 第二次返回合法 JSON，解析成功，用户无感知。

### 场景 4：历史会话管理

1. 用户在 `/tasks/new` 完成场景 1 的对话。
2. 用户点击「历史会话」按钮，抽屉展示刚才的会话，标题为「开发一个登录接口...」。
3. 用户关闭抽屉后刷新页面，再次打开「历史会话」，选择该会话继续。
4. 页面加载历史消息和三件套，用户可继续对话。
5. 用户点击「新建会话」，页面清空，可开始新对话。
6. 用户删除某条历史会话，抽屉中不再展示。

## 6. CI 门禁

- 后端：`pytest` 全部通过（当前后端测试基线 79 passed，本次新增约 25 个测试后目标基线为 104 passed）。
- 后端：`ruff check .` 通过。
- 后端：`python scripts/check_file_size.py` 通过（新增后端文件均不超过 300 行业务文件 / 250 行路由文件上限）。
- 前端：`npm run typecheck` 通过。
- 前端：`npm run lint` 通过。
- 前端：`npm run test` 通过（新增至少 2 个组件/API 测试）。
- 前端：`npm run build` 通过。
- 前端：`npm run test:e2e:ci` 通过（至少覆盖场景 1 和场景 4）。
- AI 调用相关测试必须使用 `unittest.mock.AsyncMock` 或 `respx` mock，禁止真实调用 DeepSeek API 消耗 token。
- 数据库 session fixture 必须在事务内回滚，不污染 `Task` 和 `TaskBuildingSession` 表历史数据。

## 7. 测试数据与隔离

- 每个测试使用独立的数据库事务（`db_session` fixture），测试结束后回滚。
- 派发测试需要在事务内插入 `Task` 记录（带 `specification`），测试结束回滚，不污染任务队列。
- 历史会话测试插入 `TaskBuildingSession` 记录，测试结束回滚。
- `httpx.AsyncClient` 通过 `unittest.mock.patch` 替换，禁止真实 HTTP 调用打到中枢自身。
- `SpecBuilderService.chat` 和 `SpecBuilderService.chat_stream` 在 API 集成测试中通过 `patch` mock，避免触发真实 LLM 调用。
- `start_scheduler` 和 `start_heartbeat_monitor` 在 `http_client` fixture 中通过 `_noop_start_background_task` 替换，避免后台任务干扰测试。
- 前端单元/集成测试使用 MSW 拦截 API，不依赖真实后端；E2E 测试在 CI 中启动真实后端服务。

## 8. 前端验证情况

前端 `/tasks/new` 页面基础版本已开发完成并通过手动浏览器测试：

- `src/pages/TaskNew.tsx`：左右分栏布局，左侧聊天区（消息列表 + 输入框 + 发送按钮），右侧三件套 textarea 编辑区（Tabs 切换 spec/tasks/checklist），底部「创建任务」按钮受 `isReady` 控制。
- `src/api/taskBuilding.ts`、`src/api/tasks.ts`：API 对接已完成（同步对话、创建任务、派发）。
- `src/pages/TaskQueue.tsx`：任务队列对 `source=spec_chat` 的任务增加「选择子系统执行」下拉框。

**本次需要重构/新增的前端内容**：

- 将 `taskBuildingChat` 从同步 `axios.post` 改为 `EventSource` 流式调用 `POST /api/v1/tasks/build/chat/stream`。
- 新增 `src/api/taskBuildingSessions.ts`：历史会话 CRUD API 封装。
- 扩展 `TaskNew.tsx`：顶部工具栏显示当前模型、fast/thinking 模式切换、历史会话抽屉入口。
- 新增 `TaskSessionDrawer` 组件（或内联在 `TaskNew.tsx` 中）：历史会话列表、继续、删除、新建会话。
- 按 [ADR-011](../adr/011-frontend-testing-framework.md) 引入 Vitest + RTL + Playwright + MSW 测试框架。

**自动化测试目标**：

- 前端组件单元测试：`TaskNew.tsx` 模式切换、SSE 消息渲染、创建按钮禁用逻辑、历史会话抽屉交互。
- 前端 API 对接测试：`taskBuildingChatStream`、`taskBuildingSession` CRUD、`createTask`、`dispatchTask` 的 MSW 拦截测试。
- E2E 浏览器自动化：场景 1（SSE 对话 → 创建任务）和场景 4（历史会话管理）的完整用户流。

## 9. 中枢 MOCK 接口说明

根据 ADR-010 决策点 5，MVP 阶段派发闭环采用**中枢 mock**：

- `POST /api/v1/subsystems/{id}/accept-task` 由中枢后端实现（`app/api/v1/endpoints/task_dispatch.py` 的 `subsystem_router`），模拟子系统接收任务并返回固定响应 `{"status":"accepted"}`。
- `TaskService.dispatch_task` 通过 `httpx.AsyncClient` 调用中枢自身的 mock 接口，更新 `task.execution_target` 和 `task.status=assigned`。
- **待真实子系统接入后**：`accept-task` 接口迁移至各 Agent 节点暴露真实端点，中枢 `dispatch_task` 改为正向转发请求到目标子系统地址（需引入子系统注册与地址发现机制，超出本 ADR 范围）。

该 mock 设计已在 harness 第 4.2 节集成测试和第 5 节场景 2 中覆盖。
