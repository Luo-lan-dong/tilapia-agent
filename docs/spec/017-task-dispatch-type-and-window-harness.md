# OPC 任务派发模式（手动 / 自动）+ 分配节点显示名称 · 测试契约

> 本文档配套 ADR-017 / [017-task-dispatch-type-and-window-spec.md](./017-task-dispatch-type-and-window-spec.md)。

## 1. 测试金字塔

```
            ┌──────────────────┐
            │   E2E（1 场景）   │  Playwright + 真后端
            └────────┬─────────┘
                     │
          ┌──────────┴──────────┐
          │ 集成 / 契约（6 场景）│  真实 PG + httpx AsyncClient
          └──────────┬──────────┘
                   │
            ┌──────┴──────┐
            │ 单元（10 场景）│  纯函数 / mock
            └─────────────┘
```

| 层 | 数量 | 跑分 | 负责人 |
|----|------|------|--------|
| 单元 | 10 | pytest -m unit | AI |
| 集成 / 契约 | 6 | pytest -m integration | AI |
| E2E | 1 | Playwright | AI |

## 2. 单元测试（10 项）

### 2.1 中枢 Service `TaskService.create_task` 与 `dispatch_task`（5 项，落在 `tests/services/test_task_service.py`）

| 用例 | 描述 | 期望 |
|------|------|------|
| `test_create_task_sets_dispatch_window_until` | 创建任务时 `dispatch_window_until` ≈ `now + TASK_DISPATCH_WINDOW_SECONDS` | 落库 `dispatch_window_until` 存在且差值 ≈ 配置值 |
| `test_create_task_default_dispatch_type_is_auto` | 不传 dispatch_type 时新建任务默认 `auto` | 落库 `dispatch_type='auto'` |
| `test_manual_dispatch_writes_manual_and_clears_window` | 调用 `dispatch_task` | `dispatch_type='manual'`，`dispatch_window_until IS NULL`，`status='pending'`，`assigned_node_id IS NULL` |
| `test_manual_dispatch_allows_non_spec_chat_source` | 任务 `source='manual'` 时手动 dispatch 不报错 | 调用成功，状态写入正确 |
| `test_manual_dispatch_rejects_running_task` | `status='running'` 时手动 dispatch | 抛 `TaskDispatchError`（沿用现有 400 行为） |

### 2.2 中枢 Service `TaskService.list_tasks`（3 项，落在 `tests/services/test_task_service.py`）

| 用例 | 描述 | 期望 |
|------|------|------|
| `test_list_tasks_returns_assigned_node_name` | 已分配节点的任务 | `assigned_node_name` = `agent_nodes.name` |
| `test_list_tasks_returns_null_assigned_node_name` | 未分配节点（assigned_node_id IS NULL） | `assigned_node_name IS NULL`（outerjoin） |
| `test_list_tasks_returns_dispatch_type_and_window` | 任意任务 | 响应含 `dispatch_type`（`auto`/`manual`）与 `dispatch_window_until`（datetime 或 null） |

### 2.3 中枢 Scheduler `dispatch_pending_tasks`（2 项，落在 `tests/services/test_scheduler.py`）

| 用例 | 描述 | 期望 |
|------|------|------|
| `test_scheduler_skips_manual_dispatch_type` | 1 条 `dispatch_type='manual'` 的 pending 任务 + 1 条 `auto` 的 | 只有 `auto` 那条被派发；`manual` 仍 pending |
| `test_scheduler_skips_window_not_yet_expired` | 1 条 `dispatch_window_until = now() + 60s` 的 pending 任务 + 1 条 `now() - 1s` 的 | 只有窗口已过那条被派发 |

## 3. 集成 / 契约测试（6 项，真实 PostgreSQL + Alembic）

### 3.1 Alembic 迁移（2 项，落在 `tests/alembic/test_20260827000000_add_dispatch_type_and_window_to_tasks.py`）

| 用例 | 描述 | 期望 |
|------|------|------|
| `test_migration_creates_dispatch_type_column` | 升级 head，检查 `information_schema.columns` | `dispatch_type` 列存在，default `'auto'`，NOT NULL |
| `test_migration_creates_dispatch_window_until_column` | 同上 | `dispatch_window_until` 列存在，nullable |
| `test_migration_creates_dispatch_type_index` | 检查 `pg_indexes` | 复合索引 `idx_tasks_dispatch_type (dispatch_type, dispatch_window_until)` 存在 |

### 3.2 PATCH API 契约（沿用 task_service；不重复）

### 3.3 任务列表 API 契约（3 项，落在 `tests/api/v1/test_tasks.py`）

| 用例 | 描述 | 期望 |
|------|------|------|
| `test_list_tasks_response_includes_dispatch_type` | 任意 GET /tasks | 响应 items[].dispatch_type ∈ {'auto','manual'} |
| `test_list_tasks_response_includes_assigned_node_name` | 节点名为 "笔记本" 的任务 | items[].assigned_node_name == "笔记本" |
| `test_dispatch_task_response_includes_dispatch_type_manual` | POST /tasks/{id}/dispatch | 响应 data.dispatch_type == "manual" |

## 4. E2E（1 项，Playwright + 真实中枢）

### 4.1 任务派发窗口手动阻断场景

**前置**：
- 启动 PostgreSQL / Redis / 中枢 / 前端。
- 设置 `TASK_DISPATCH_WINDOW_SECONDS=30`（便于测试）。
- 启动至少 1 个 Agent 节点（agent_type='code'，status='idle'）。

**步骤**：
1. 打开 `http://localhost:5173/tasks/new`，新建一个 `agent_type='code'` 的任务，记下 task_id。
2. 立即打开 `http://localhost:5173/tasks`，截图「派发」列应显示「自动」（dispatch_type=auto）。
3. 等待 5 秒（仍在窗口期内），打开任务详情，点「手动派发」，选子系统，保存。
4. 回到任务列表，刷新，看到「派发」列显示「手动」。
5. 等待 60 秒（远超窗口期 30 秒），任务**不应**被自动派发（列表里仍 pending）。
6. 后端 logs grep `task_manual_dispatched` 命中 1 条。
7. 关闭所有服务。

**期望**：
- 任务始终在「手动」模式，未被自动派发。
- 后续手动 dispatch 接口不会再次写（已是 manual）。

## 5. CI 门禁

PR 合并前必须通过：

1. `ruff check app tests --select E,F,W` —— 无错误。
2. `pytest tests/services/ tests/api/v1/test_tasks.py tests/alembic/ -m "unit or integration"` —— 全部通过。
3. `cd opc-web-console && pnpm typecheck` —— 无错误。
4. `cd opc-web-console && pnpm lint` —— 无错误（只检查我改的 2 个文件）。
5. 手动执行 E2E 1 场景通过 + 截图归档。

## 6. 失败重试与 flaky 处理

- 集成测试中 `dispatch_window_until` 字段的差值校验允许 ±2 秒抖动（PG 写入延迟）。
- E2E 涉及 PostgreSQL 写入时延，窗口设为 30 秒避免与调度器 10 秒周期叠加。

## 7. 关联文档

- [ADR-017](../adr/017-task-dispatch-type-and-window.md)
- [017-task-dispatch-type-and-window-spec.md](./017-task-dispatch-type-and-window-spec.md)
- [002-central-dispatch-hub-harness.md](./002-central-dispatch-hub-harness.md) — 通用测试金字塔
- [api-standards.md](../standards/api-standards.md) — 业务错误码
- [logging-standards.md](../standards/logging-standards.md) — 结构化日志规范
