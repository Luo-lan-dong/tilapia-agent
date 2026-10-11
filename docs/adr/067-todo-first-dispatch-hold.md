# ADR-067 待办先行与派发确认门禁（orchestrator-todo-first-dispatch-hold）

| 项目 | 内容 |
|---|---|
| 状态 | Accepted（2026-10-07 用户实测反馈，诉求明确：①收到需求第一件事列中枢待办；②创建任务后必须等确认，任何东西都不许自动派发） |
| 日期 | 2026-10-07 |
| 关联文档 | ADR-033（编排与准则 11「确认后派发」）、ADR-061 M5d（中枢待办）、ADR-066（终态唤醒） |

## 1. 问题背景（2026-10-07 用户实测两截图）

**问题 A 待办后置**：实际顺序为「生成三件套 → 创建任务 → 才维护中枢待办」，
与用户期望「收到需求第一件事列出全计划待办（生成三件套/创建任务/等待确认…）再逐项执行」相反。
根因：中枢待办 FK 挂 `tasks.id`，任务不存在时 `todo_write` 明确失败
（`orchestrator_todo.py`：「本会话尚未关联任务」）——鸡生蛋：待办依赖任务，任务依赖三件套。

**问题 B 确认门禁被调度器击穿**：编排 AI 完全遵守准则 11（任务停 pending、报告「等待你确认派发」、
未调 smart_dispatch），但**调度器** `dispatch_pending_tasks`（scheduler.py:544）周期扫描
`pending + dispatch_type=auto` 任务并在数秒内自动派发到空闲节点——AI 消息显示「等待确认」的同时
节点已在执行（24%）。提示词约束只管 AI，管不住调度器。

## 2. 决策

### 2.1 待办先行（会话级待办 + 建任务迁移）

- `task_orchestrator_todos` 加 `session_id`（nullable，FK task_building_sessions）；
  `task_id` 保持 nullable——两者至少其一非空。
- `todo_write` 解析顺序调整：任务关联存在 → 挂任务（原语义不变）；**无任务时挂当前会话**
  （`session_id` 维度），不再报错。
- `create_task` 成功后：把该会话名下 `task_id IS NULL` 的待办**迁移**到新任务（UPDATE task_id）。
- 查询端点 `GET /tasks/{task_id}/orchestrator-todos`：返回 `task_id=该任务` 的待办
  （迁移后历史待办都归属任务，无需合并逻辑）。
- 提示词准则 3 改回**待办真先行**：收到执行型需求后第一件事 `todo_write(add)` 列出全计划
  （含「生成三件套」「创建任务」「等待用户确认派发」），再逐项执行；删除「先 create_task（技术前提）」
  的顺序约束。

### 2.2 派发确认门禁（dispatch_hold）

- `tasks` 加 `dispatch_hold: bool`（默认 False，注释「等待用户确认派发，调度器跳过」）。
- `create_task`（编排 AI 路径）创建的任务默认 `dispatch_hold=True`——pending 但调度器不可见。
- 调度器扫描 where 增加 `Task.dispatch_hold.is_(False)`。
- 解除：`TaskService.dispatch_task`（smart_dispatch 与手动派发的共同路径）执行时置 False——
  用户确认后 AI 调 smart_dispatch 即自然放行；用户在任务队列手动派发同样放行。
- 语义：`dispatch_hold=True` 的任务对用户呈现为「待确认派发」（前端徽标可选，M4 后置）。

## 3. 备选方案

| 方案 | 说明 | 结论 |
|---|---|---|
| A1 待办挂会话 + 迁移（本决策） | 一次迁移支持「先行」，语义清晰 | ✅ |
| A2 待办改为纯会话维度（不挂任务） | 破坏 ADR-061 M5d 任务级语义与既有查询 | ✗ |
| B1 dispatch_hold 布尔（本决策） | 最小改动、调度器一处 where | ✅ |
| B2 新状态 `awaiting_confirm` | 状态机膨胀，牵动前端/调度/展示多处 | ✗ |
| B3 关闭调度器自动派发 | 破坏 API 直建任务的既有自动化 | ✗ |

## 4. 影响

- 迁移一张（todos.session_id + tasks.dispatch_hold）；todo 服务/skill、task_creation、
  scheduler、dispatch_task、提示词、测试。
- 兼容性：既有待办行 session_id 为 NULL 不受影响；既有任务 dispatch_hold 默认 False 行为不变。
- 红线：调度器跳过逻辑不得影响 API 直建任务（无 hold）的自动派发；待办迁移失败不阻塞创建任务（旁路）。

## 5. 实际实施情况

（开发完成后回填）
