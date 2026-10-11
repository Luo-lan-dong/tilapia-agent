# ADR-015：任务卡死检测与自动失败兜底

## 状态

Accepted

## 日期

2026-08-25

## 问题背景

2026-08-25 巡检发现 2 个任务长期处于 `running` 状态却无任何进度推进：

| 任务 ID | 状态 | 进度 | 最后更新（UTC） | 分配节点 | 卡死时长 |
|---|---|---|---|---|---|
| ac74cb06-c259-4b22-a232-922bda5b120d | running | 90% | 2026-08-25 16:34:33 | 3f7bb90f-...-b6d69c | >24 小时 |
| d1f16732-e483-46ba-a786-c955b88e5330 | running | 47% | 2026-08-24 16:35:50 | （已无） | >2 天 |

任务队列与节点集群页持续显示这 2 个任务「执行中」，节点被长期占用却无实际工作产出（也未上报 `agent.task_completed` / `agent.task_failed`）。

### 根因

- Agent 进程崩溃或 WebSocket 断连时，`agent_node_ws.py` 的 `finally` 块仅把节点状态置为 `offline`（见 `agent-node-standards.md` §4.2）。
- 节点 `offline` 后，**已分配给它但仍在 `running` 状态的任务不会被自动标 failed**：
  - `scheduler.py` 只扫描 `pending` 任务进行派发，从未扫描 `running` 任务做健康检查。
  - `task_event_handler._handle_task_failed` 仅由 Agent 主动上报触发。
  - `_handle_task_progress` 也不处理"长期无心跳"。
- 结果：Agent 端异常 → WebSocket 断开 → 节点 offline → 任务「僵尸」占用节点与前端展示位。

### 需求

1. 中枢必须**自动检测**「分配了节点但长期无进度」的任务，并把它们标记为 `failed`。
2. 节点必须**同时释放**（load 减 1、status 回到 idle，便于后续新任务派发）。
3. 失败原因必须**可追溯**（写 `change_summary` 或 `plan_content`，前端可见）。
4. 阈值必须**可配置**（不同 Agent 类型执行时长差异巨大，但 MVP 仅一种 `programming`，默认 30 分钟）。
5. 不能影响正常任务：未到阈值的 `running` 任务继续轮询，不被误杀。

## 决策

### 1. 检测位置：复用 `scheduler.py` 后台循环

将卡死检测作为现有调度器的一次性扫描步骤，与 `_dispatch_once()` 共用同一 10 秒间隔的后台任务。理由：

- 调度器已用 `asyncio.create_task` 在 FastAPI lifespan 启动，复用避免新增后台组件。
- 调度器已有 `AsyncSessionLocal` 与 `connection_manager` 的依赖注入习惯，扩展成本最低。
- 检测频次 10 秒与派发频次一致，对数据库读压力增加约 1 个 `UPDATE ... WHERE updated_at < NOW() - INTERVAL`。

### 2. 判定标准：`running` 且 `updated_at` 早于「现在 - 阈值」

复用 `tasks.updated_at` 作为"最后活动时间"：所有写任务行的路径（`_handle_task_started` / `_handle_task_progress` / `_handle_task_completed` / `_handle_task_failed`）都会刷新 `updated_at`，因此只要 Agent 在上报任何事件，时间戳就会更新。

- 判定 SQL：
  ```sql
  SELECT id, assigned_node_id
  FROM tasks
  WHERE status = 'running'
    AND updated_at < NOW() - (:threshold * INTERVAL '1 second')
    AND assigned_node_id IS NOT NULL
  FOR UPDATE SKIP LOCKED
  ```
- 阈值默认 `STALE_TASK_TIMEOUT_SECONDS = 1800`（30 分钟），通过 Pydantic Settings 注入。
- `FOR UPDATE SKIP LOCKED` 避免与并发 `task_progress` 事件处理器抢锁。

### 3. 兜底动作：标 failed + 释放节点 + 记录原因

在同一个事务里完成：

```python
task.status = TaskStatus.FAILED.value
task.change_summary = "任务执行超过 {timeout}s 无进度，自动标记为 failed"
# 若任务尚未收到 change_summary（即未到 completed），才覆写
# 已有 change_summary 的不覆写（completed 失败回调场景）
await session.execute(text("""
    UPDATE agent_nodes
    SET status = 'idle', load = GREATEST(load - 1, 0)
    WHERE id = :nid
"""), {"nid": task.assigned_node_id})
```

复用现有 `_release_node` 助手（见 `task_event_handler.py` §3）保证释放逻辑一致。

### 4. 配置项：环境变量 + Pydantic Settings

新增环境变量 `STALE_TASK_TIMEOUT_SECONDS`，缺省 `1800`，最小 `60`（防止误配置为 0 导致立即失败）。

纳入 `environment-config-standards.md` §2 的标准配置清单。

### 5. 失败原因落库：复用 `change_summary`

复用 `change_summary` 字段记录自动失败原因（与 build 模式本轮修改说明共用字段），因为：

- 字段已是 `text` 类型，存短句"自动失败原因"无类型冲突。
- 前端 `TaskNewDocPanel`「方案」Tab 已展示该字段，无需新增展示位。
- 仅在 `change_summary IS NULL` 时覆写，避免覆盖 Agent 已上报的有效说明。

### 6. 可观测性

- 每次兜底动作记录 `task_stale_failed` `INFO` 日志（含 task_id、node_id、stale_seconds）。
- 启动时记录 `stale_task_detector_started` 含 threshold。
- 复用现有 `logging-standards.md` §3 结构化字段。

## 备选方案

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| A（本决策）：scheduler 复用 + SQL 扫描 + `change_summary` 兜底 | 改动最小；复用现有后台任务与助手；阈值可配置 | 每 10 秒多一次全表扫描（当前 `running` 任务数 < 10，无问题） | ✅ 选中 |
| B：新建独立 `stale_task_detector.py` 后台组件 | 职责单一；易独立测试 | 引入新的生命周期管理；与 scheduler 重复 | ❌ 否决 |
| C：依赖 Agent 端心跳检查"任务执行超时"（见 `agent-node-standards.md` §5.3） | Agent 端已有超时配置 | 需要所有 Agent 模板升级；WebSocket 断连后 Agent 本身已死，无法上报 | ❌ 否决（只治标） |
| D：用 `change_summary` 之外的新字段 `failure_reason` | 语义更清晰 | 需要 Alembic 迁移；前端展示层无差异 | ⚠️ 延后（合并到「统一失败原因字段」专题） |
| E：仅释放节点不标 failed | 简单 | 任务仍占队列「执行中」位置；用户仍困惑 | ❌ 否决 |

## 决策原因

1. **复用最大化**：不引入新后台任务、新字段、新事件，仅在现有 scheduler 中追加 1 个步骤。
2. **数据库压力可控**：`running` 任务数极小（< 10），10 秒一次的 `SELECT ... FOR UPDATE` 可忽略。
3. **阈值可调**：通过环境变量即可调整，无需发版。
4. **避免误杀**：`SKIP LOCKED` 与状态机检查（`status='running'`）保证不干扰正常任务。
5. **前端零改动**：`change_summary` 字段已存在，自动失败原因直接展示给用户。

## 影响

### 正面影响

- 任务队列不再出现"假执行中"，提升用户对系统可信度。
- 节点资源（load、status）能自动恢复空闲，新任务可正常派发。
- 历史数据中遗留的"僵尸"任务可一次性清理（本变更已通过一次性脚本 `scripts/fail_stuck_tasks_20260825.py` 处理 2 条历史数据）。

### 负面影响 / trade-off

- 每 10 秒一次的全 `running` 任务扫描，随业务规模增长需要后续优化（分区表 / 索引 hint）。MVP 阶段可忽略。
- 若 Agent 在 30 分钟内合法完成"长任务"（如大型构建），会被误杀。`STALE_TASK_TIMEOUT_SECONDS` 可调，且对长任务可考虑后续加"心跳延展"事件。

### 需要做的变更

- 中枢后端：
  - `scheduler.py` 新增 `_fail_stale_tasks()` 协程与 `_dispatch_once()` 共用循环。
  - 新增 `STALE_TASK_TIMEOUT_SECONDS` Pydantic Settings 字段。
  - `task_event_handler.py` 复用 `_release_node` 助手。
  - 新增 Alembic 迁移（**不需要**：复用现有字段）。
- 一次性脚本：`scripts/fail_stuck_tasks_20260825.py`（已在变更中提交）。
- 文档：
  - 新增 Spec：`015-task-stale-detector-spec.md`。
  - 新增 Harness：`015-task-stale-detector-harness.md`。
  - 更新 `002-central-dispatch-hub-spec.md`：补充「任务卡死兜底」小节。
  - 更新 `004-agent-node-protocol-spec.md`：补充 Agent 长期断连时的兜底说明。
  - 更新 `environment-config-standards.md`：登记 `STALE_TASK_TIMEOUT_SECONDS`。
  - 更新 `INDEX.md` 1.1/1.2 与"六、文档更新记录"。

## 关联文档

- Spec：[015-task-stale-detector-spec.md](../spec/015-task-stale-detector-spec.md)
- Harness：[015-task-stale-detector-harness.md](../spec/015-task-stale-detector-harness.md)
- 相关规范：[api-standards.md](../standards/api-standards.md)、[agent-node-standards.md](../standards/agent-node-standards.md)、[database-standards.md](../standards/database-standards.md)、[logging-standards.md](../standards/logging-standards.md)、[environment-config-standards.md](../standards/environment-config-standards.md)
- 一次性清理脚本：[opc-central-dispatch-hub/scripts/fail_stuck_tasks_20260825.py](../../opc-central-dispatch-hub/scripts/fail_stuck_tasks_20260825.py)
- 关联 ADR：[ADR-012（tilacode 执行层集成）](./012-tilacode-execution-layer-integration.md)、[ADR-004（节点通信协议）](./004-communication-protocol.md)

## 实际实施情况

- 已通过一次性脚本 `scripts/fail_stuck_tasks_20260825.py` 清理 2 条历史"僵尸"任务（ac74cb06、d1f16732），释放节点 3f7bb90f-...-b6d69c。
- 本 ADR 的 `scheduler.py` 自动检测与 `STALE_TASK_TIMEOUT_SECONDS` 配置项开发待 `015-task-stale-detector-spec.md` 审阅后开展。

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-08-25 | 初始创建：决策任务卡死检测与自动失败兜底方案（scheduler 复用 + SQL 扫描 + `change_summary` 落库） | AI助手 |
