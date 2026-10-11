# tilacode 执行层集成 Harness

## 1. 测试策略

| 层级 | 目标 | 工具 |
|------|------|------|
| 单元测试 | tilacode 进程管理、HTTP API 客户端、事件转换器、工作目录管理 | pytest + pytest-asyncio |
| 集成测试 | 任务桥接全链路（接收任务 → 调用 tilacode → 上报进度） | pytest + TestClient + tilacode server |
| 契约测试 | tilacode HTTP API 契约、中枢 WebSocket 事件格式 | pytest-asyncio + httpx + websockets |
| 端到端测试 | 真实编程任务从下发到产物上传的全流程 | 手动 + 中枢联调 |

## 2. 单元测试清单

### 2.1 tilacode 进程管理（tilacode_manager.py）

- [ ] 能启动 tilacode server 子进程并等待就绪
- [ ] 能停止 tilacode server 子进程并清理资源
- [ ] 健康检查通过时返回 True
- [ ] 健康检查失败时返回 False
- [ ] tilacode server 崩溃后能自动重启
- [ ] 重启次数超阈值时标记 unavailable 并停止重试

### 2.2 tilacode HTTP API 客户端（tilacode_client.py）

- [ ] `create_session(directory)` 返回 session_id
- [ ] `prompt(session_id, message)` 成功发送 prompt
- [ ] `subscribe_events(session_id)` 返回异步事件流
- [ ] `interrupt(session_id)` 成功中断会话
- [ ] `get_history(session_id)` 返回会话历史
- [ ] API 调用超时时抛出 `TilacodeTimeoutError`
- [ ] API 返回错误时抛出 `TilacodeAPIError` 并包含错误详情
- [ ] 连接被拒时抛出 `TilacodeConnectionError`

### 2.3 事件转换器（event_translator.py）

- [ ] `session.next.text.delta` / `ended` → `agent.task_progress`（含文本内容）
- [ ] `session.next.reasoning.delta` / `ended` → `agent.task_progress`（含思考过程）
- [ ] `session.next.tool.called` → `agent.task_progress`（含工具名和输入）
- [ ] `session.next.tool.progress` → `agent.task_progress`（含进度内容）
- [ ] `session.next.tool.success` → `agent.task_progress`（含结果和输出路径）
- [ ] `session.next.tool.failed` → `agent.task_progress`（含错误信息）
- [ ] `session.next.step.ended` → 触发任务完成判定信号
- [ ] `session.next.step.failed` → 触发 `task_failed` 信号
- [ ] `session.error` → 触发 `task_failed` 信号
- [ ] `permission.v2.asked`（来自 `/api/event`）→ 自动回复 `"once"`
- [ ] 未知事件类型不崩溃，记录 warning 日志

### 2.4 工作目录管理（workspace.py）

- [ ] `create_workspace(task_id)` 在 `/workspace/{task_id}/` 创建目录
- [ ] `create_workspace(task_id)` 目录已存在时不报错（支持续接）
- [ ] `write_documents(task_id, documents)` 将中枢文档写入工作目录
- [ ] `archive_workspace(task_id)` 打包工作目录为 tar.gz
- [ ] `archive_workspace(task_id)` 打包后返回文件路径
- [ ] `get_workspace_path(task_id)` 返回工作目录绝对路径
- [ ] 磁盘空间不足时抛出 `WorkspaceDiskFullError`

### 2.5 任务桥接（task_bridge.py）

- [ ] 接收 `hub.task_dispatch` 后调用 `create_workspace` + `create_session` + `prompt`
- [ ] 续接任务时检测工作目录已存在，复用已有 session（`ses_task_<task_id>`）
- [ ] mode 为 `"plan"` 时创建/切换到 `plan` agent
- [ ] mode 为 `"build"` 时创建/切换到 `build` agent
- [ ] 同一 task 内先 plan 后 build 时，保留工作目录上下文
- [ ] 任务完成后调用 `archive_workspace` 并上报 `agent.task_result`
- [ ] 接收 `hub.task_interrupt` 后调用 `interrupt` 并上报 interrupted
- [ ] tilacode server 不可用时上报 `task_failed`（原因：executor_unavailable）

## 3. 集成测试清单

### 3.1 tilacode server 进程管理

- [ ] 启动 tilacode server 后，健康检查端点返回 200
- [ ] 停止 tilacode server 后，健康检查端点不可达
- [ ] 强制 kill tilacode server 进程后，Python 层在 30 秒内自动重启

### 3.2 任务执行全链路

- [ ] 发送简单编程任务（"创建一个 hello.py 文件"），验证：
  - session 创建成功（id 为 `ses_task_<task_id>`）
  - prompt 发送成功
  - 事件流收到 `session.next.tool.called`（write 工具）
  - 事件流收到 `session.next.tool.success`
  - 事件流收到 `session.next.step.ended`
  - 工作目录中存在 hello.py
  - 中枢收到 agent.task_progress 事件
  - 中枢收到 agent.task_result (status=completed)
- [ ] 发送含文档的任务，验证文档被写入工作目录
- [ ] 发送会触发 bash 命令的任务，验证 bash 工具事件被上报
- [ ] 验证无权限请求弹窗/卡住（配置 `"*": "allow"` 生效）

### 3.3 todo 列表实时上报与进度

- [ ] 发送会触发 AI 创建 todo 列表的任务（如多步骤任务），验证：
  - Agent 事件流收到 `todo.updated`
  - `agent.task_progress` payload 携带 `todos` 数组
  - 中枢 `tasks.todos` 字段被写入
  - 中枢 `tasks.progress` 按 `completed / total` 更新
- [ ] 发送不会创建 todo 列表的任务，验证：
  - 进度仍按原有事件计数机制递增
  - `agent.task_progress` 不携带 `todos` 或携带空数组
- [ ] 验证 todo 列表中 `in_progress` 项变更时，前端任务摘要面板高亮当前执行项
- [ ] 验证任务完成后 `tasks.progress` 为 100，且 `todos` 保留最终状态

### 3.4 任务续接

- [ ] 对已完成任务的 task_id 发送修改指令，验证：
  - 复用原工作目录
  - session 通过 `ses_task_<task_id>` 复用
  - 修改后的文件存在于工作目录
- [ ] 对已过期的 session（tilacode 重启后），验证：
  - Python Agent 通过相同 id 重新创建/恢复 session
  - 历史上下文可继续对话
  - 修改后的文件存在于工作目录

### 3.4 任务中断

- [ ] 任务执行中发送中断指令，验证：
  - tilacode session.interrupt 调用成功
  - 中枢收到 agent.task_result (status=interrupted)
  - 工作目录保留

### 3.5 产物上传

- [ ] 任务完成后，产物 tar.gz 成功上传到中枢文件存储
- [ ] 上传失败时重试 3 次
- [ ] 重试仍失败时，task_completed 标注"产物上传失败"

## 4. 契约测试清单

### 4.1 tilacode HTTP API 契约

- [ ] `POST /api/session` 请求体和响应体符合 tilacode API schema
- [ ] `POST /api/session/{id}/agent` 请求体和响应体符合 schema
- [ ] `POST /api/session/{id}/prompt` 请求体和响应体符合 schema
- [ ] `GET /api/session/{id}/event` 返回有效的 SSE 流
- [ ] `GET /api/event` 返回有效的 SSE 流（含权限事件）
- [ ] `POST /api/session/{id}/permission/{requestID}/reply` 返回 204 No Content
- [ ] `POST /api/session/{id}/interrupt` 返回 204 No Content
- [ ] `GET /api/session/{id}/history` 返回符合 schema 的历史数据

### 4.2 中枢 WebSocket 事件契约

- [ ] `agent.task_progress` 事件格式符合 004-agent-node-protocol-spec.md
- [ ] `agent.task_result` 事件格式符合 004-agent-node-protocol-spec.md
- [ ] `task_completed` 事件格式符合 004-agent-node-protocol-spec.md
- [ ] `task_failed` 事件格式符合 004-agent-node-protocol-spec.md
- [ ] `agent.task_progress` 携带 `todos` 时，中枢能正确解析并写入 `tasks.todos`
- [ ] `TaskResponse` 包含 `todos` 字段且类型为数组或 null

## 5. E2E 测试场景

### 场景 1：简单编程任务全流程

1. 在中枢创建一个编程任务："创建一个 Python 文件 hello.py，内容为 print('Hello World')"
2. 中枢将任务分配给在线的编程 Agent
3. Agent 启动 tilacode 执行，中枢前端实时显示执行进度
4. 任务完成后，中枢收到产物包并解压展示 hello.py 内容
5. 验证产物内容正确

### 场景 2：多轮修改

1. 执行场景 1 的任务
2. 对同一 task_id 下发修改指令："修改 hello.py，改为打印 'Hello OPC'"
3. Agent 在原工作目录续接修改
4. 任务完成后，验证 hello.py 内容已更新
5. 验证工作目录保留了修改历史

### 场景 3：任务中断

1. 下发一个耗时较长的编程任务
2. 任务执行过程中，中枢发送中断指令
3. 验证 Agent 停止执行并上报 interrupted 状态
4. 验证工作目录保留当前进度

### 场景 4：tilacode server 崩溃恢复

1. 下发一个编程任务，任务执行中
2. 手动 kill tilacode server 进程
3. 验证 Python 层在 30 秒内自动重启 tilacode server
4. 验证当前任务被标记为 failed 并上报中枢
5. 验证 Agent 恢复 idle 状态，可接收新任务

### 场景 5：含文档的复杂任务

1. 中枢下发任务，附带 spec.md 文档（含需求说明）
2. Agent 将文档写入工作目录
3. tilacode 读取文档并按需求生成代码
4. 验证生成的代码符合 spec.md 中的需求
5. 验证产物包包含 spec.md 和生成的代码文件

### 场景 6：plan → build 切换

1. 中枢下发 plan 任务："为需求 X 编写技术方案"
2. Agent 切换为 plan agent，生成 `.opencode/plans/plan.md`
3. Python 层读取 plan 产物并上报中枢
4. 中枢审批通过后，对同一 task_id 下发 build 任务："按方案实现"
5. Agent 切换为 build agent，在同一 Session/工作目录下执行代码开发
6. 验证最终代码符合 plan.md 中的方案

### 场景 7：todo 列表实时展示与进度依据

1. 在中枢创建一个多步骤编程任务（如："创建一个 React 组件，包含输入框、列表展示和添加按钮"）
2. 任务派发后，在 TaskNew 页面观察右侧「任务摘要」面板
3. 验证 Agent 开始执行后，todo 列表随 `agent.task_progress` 实时更新
4. 验证当前 `in_progress` 项高亮显示
5. 验证进度条按 `completed / total` 增长
6. 任务完成后验证 todo 全部标记为 completed，进度为 100%
7. 收起文档面板，验证图标栏可正常展开并切换 Tab
8. 刷新页面，验证 todo 列表与进度从 `tasks.todos` / `tasks.progress` 恢复

### 场景 8：tilacode 更新后历史会话保留

1. 执行一个任务，记录 session_id
2. 重新运行 Agent 安装脚本（模拟 tilacode 更新）
3. 对同一 task_id 下发修改指令
4. 验证 Agent 能复用原 session_id 并继续对话
5. 验证 `~/.local/share/opencode/` 下的数据未被删除

## 6. CI 门禁

- [ ] `ruff` 代码风格检查通过（Python 层）
- [ ] `pytest` 单元测试 + 集成测试全部通过
- [ ] `python scripts/check_file_size.py` 文件长度检查通过（单文件 ≤ 300 行）
- [ ] tilacode 改造代码 `bun typecheck` 通过（如涉及 TS 改动）
- [ ] tilacode 改造代码 `oxlint` 通过（如涉及 TS 改动）

## 7. 测试环境要求

### 7.1 单元/集成测试

- Python 3.10+
- Bun 1.3+（运行 tilacode）
- tilacode 项目代码（已包含在 opc-agent-programming/tilacode/）
- 可用的 LLM API Key（用于 tilacode 执行，测试环境用低成本模型）

### 7.2 E2E 测试

- 完整的中枢后端 + 前端
- 一台 Linux 机器（或 WSL）运行 Agent
- 中枢与 Agent 网络互通

## 8. 关联文档

- [012-tilacode-execution-layer-integration-spec.md](./012-tilacode-execution-layer-integration-spec.md)
- [ADR-012 基于 tilacode 集成编程执行层](../adr/012-tilacode-execution-layer-integration.md)
- [008-node-management-and-programming-agent-linux-harness.md](./008-node-management-and-programming-agent-linux-harness.md)
