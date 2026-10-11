# 任务卡死检测与自动失败兜底 Spec

## 1. 功能描述

为 OPC 中央调度中枢提供**任务卡死兜底**能力：

- 后台周期性扫描 `status='running'` 且 `updated_at` 早于「现在 - 阈值」的任务。
- 判定为「卡死」后，**自动**：
  1. 把任务状态置为 `failed`。
  2. 释放所属节点（`status='idle'`、`load -= 1`）。
  3. 在 `change_summary` 字段写入失败原因（仅在该字段为 NULL 时写入）。
- 记录结构化 `INFO` 日志（含 `task_id` / `node_id` / `stale_seconds`），便于排查。

阈值通过环境变量 `STALE_TASK_TIMEOUT_SECONDS` 配置，缺省 1800 秒（30 分钟），最小 60 秒。

## 2. 用户故事

- 作为任务队列用户，我希望看到「执行中」的任务一定有真实进展，避免假阳性的「永远 90%」。
- 作为运维人员，我希望节点在 Agent 崩溃后能自动恢复空闲，等待新任务派发。
- 作为调度器开发者，我希望兜底逻辑不引入新的后台任务，复用现有调度循环。

## 3. 核心流程

### 3.1 任务卡死检测与兜底

```
FastAPI lifespan 启动 scheduler.run_scheduler(stop_event)
    ↓
循环每 10 秒执行一次 _dispatch_once()
    ↓
扩展为：循环内依次执行 _dispatch_once() + _fail_stale_tasks()
    ↓
_fail_stale_tasks():
    1. SELECT ... FROM tasks
       WHERE status = 'running'
         AND updated_at < NOW() - threshold
         AND assigned_node_id IS NOT NULL
       FOR UPDATE SKIP LOCKED
    2. 对每条卡死任务：
       a. UPDATE tasks SET status='failed', change_summary=自动失败原因
          WHERE id=:tid AND status='running'  -- 状态机二次校验
       b. UPDATE agent_nodes
          SET status='idle', load=GREATEST(load-1, 0)
          WHERE id=:nid
       c. 记录 INFO 日志 task_stale_failed
    3. COMMIT
```

### 3.2 异常分支

- **节点已不在线（offline）**：兜底仍执行；`UPDATE agent_nodes` 影响行数 0 时记录 `node_already_offline` WARNING，不中断。
- **任务已被 Agent 上报 completed/failed**：兜底扫描读到后 UPDATE 状态机二次校验失败（`status != 'running'`），影响行数 0，**安全跳过**。
- **change_summary 已有内容**（如 build 模式完成后又被外部失败回滚）：覆写前用 `change_summary IS NULL` 条件保护，**不覆盖 Agent 已上报内容**。
- **多个 scheduler 实例同时运行**：`FOR UPDATE SKIP LOCKED` 互斥锁保证不会重复失败同一条任务。
- **数据库连接异常**：catch 后记录 `stale_task_detector_failed` `ERROR` 日志，不影响 `_dispatch_once()` 继续。

## 4. 数据模型

无需新增字段。复用现有：

| 表 | 字段 | 用途 |
|---|---|---|
| tasks | status | 写入 `failed` |
| tasks | change_summary | 写入自动失败原因（仅 NULL 时） |
| tasks | updated_at | 阈值判定基准 |
| tasks | assigned_node_id | 关联节点 |
| agent_nodes | status | 写回 `idle` |
| agent_nodes | load | `-= 1`（不小于 0） |

无 Alembic 迁移。

## 5. 接口

无需新增对外 HTTP 接口。`GET /v1/tasks/{id}` 与 `GET /v1/tasks?status=running` 已存在；兜底后任务自然以 `failed` 状态出现。

## 6. 配置

| 环境变量 | 缺省 | 说明 |
|---|---|---|
| `STALE_TASK_TIMEOUT_SECONDS` | `1800` | 任务卡死判定阈值（秒）。最小 60。 |

Pydantic Settings 字段：

```python
stale_task_timeout_seconds: int = Field(
    default=1800,
    ge=60,
    description="running 任务超过该秒数无任何更新则自动标 failed",
)
```

启动日志包含：`stale_task_detector_started` 事件 + `threshold_seconds` 字段。

## 7. 日志

| 事件名 | 级别 | 触发时机 | 字段 |
|---|---|---|---|
| `stale_task_detector_started` | INFO | scheduler 启动时 | `interval_seconds`, `threshold_seconds` |
| `task_stale_failed` | INFO | 每次兜底失败 | `event`, `task_id`, `node_id`, `stale_seconds`, `had_change_summary` |
| `stale_task_detector_failed` | ERROR | 异常分支 | `event`, `error` |
| `node_already_offline` | WARNING | 兜底时节点已 offline | `event`, `node_id` |

## 8. 前端

**无前端改动**：

- 任务队列页面已按 `status` 过滤，"执行中"列自动不再出现已失败任务。
- 任务详情 / 「方案」Tab 已展示 `change_summary` 字段，自动失败原因可直接显示给用户。

## 9. 降级链路

| 故障 | 降级策略 |
|---|---|
| 数据库连接断开 | `stale_task_detector_failed` ERROR 日志；下次循环自动重试 |
| agent_nodes 行不存在 | `UPDATE` 影响行数 0，记 `node_already_offline` WARNING，继续 |
| 阈值配置为极小值（如 60s） | 启动时校验 `ge=60` 防止误配；长任务可上调 |
| Agent 在 30 分钟内合法完成 | 任务 `updated_at` 持续被 `task_progress` 刷新，不会被误杀；如需更长阈值，调环境变量 |

## 10. 验收标准

1. 启动日志包含 `stale_task_detector_started`，`threshold_seconds` 等于配置值。
2. 制造一个 `running` 任务（手动 `UPDATE tasks SET status='running', updated_at=NOW() - INTERVAL '31 minutes'`），等待 10 秒：
   - 任务状态变为 `failed`。
   - 任务 `change_summary` 包含「自动标记为 failed」字样。
   - 节点状态变为 `idle`，`load` 减 1。
   - 日志出现 `task_stale_failed`。
3. 制造一个 `running` 任务（`updated_at = NOW()`），等待 10 秒：状态保持 `running`，不被兜底。
4. 制造一个 `running` 任务（`updated_at = NOW() - 31 min`），且 `change_summary` 已有内容：兜底不覆写 `change_summary`。
5. 一次性脚本 `scripts/fail_stuck_tasks_20260825.py` 已成功清理 2 条历史数据（ac74cb06、d1f16732）。
