# OPC 任务派发模式（手动 / 自动）+ 分配节点显示名称 · 业务规格说明

> 本文档配套 ADR-017，描述本特性在用户故事、核心流程、降级链路、验收标准层面的业务行为。
> 技术实现细节见 ADR-017 与相关规范。

## 1. 背景

2026-08-26 巡检发现任务队列页两个体验问题：

1. 「分配节点」列显示节点 ID（`3f7bb90f-05eb-49cc-8661-9d9e81b6d69c`）而不是节点管理页可读的名称（`笔记本` / `r410` / `localhost.localdomain-programming-j1169i`）。
2. 「派发」列固定为 `—`：分不清任务是用户手动派发的还是中枢自动派发的。
3. **关键**：「手动派发」功能在 DB 层未落地——任务创建后无论用户是否手动 dispatch，调度器都会在下一轮自动接管。

## 2. 用户故事

### US-1 看到任务的派发模式

作为 **运维**，我希望任务列表的「派发」列能直接告诉我"这个任务是手动派发还是自动派发"，不需要看日志或 SQL。

### US-2 看到分配节点的名称

作为 **运维**，我希望任务列表的「分配节点」列直接显示节点的展示名（如 `笔记本`），而不是 UUID 前 8 位。

### US-3 手动派发真的能阻断自动派发

作为 **运维**，我希望任务创建后我有 10 秒时间选择手动派发（指定子系统），10 秒内我选了，调度器就不再接管；10 秒内我没选，调度器才接管。

### US-4 调整派发窗口

作为 **运维**，我希望能通过环境变量调整"10 秒窗口"的长度（生产环境可能要 30 秒或 60 秒，dev 可能想 0）。

## 3. 核心流程

### 3.1 任务创建后默认走自动派发

```
┌─────────────────────┐
│ TaskService.create_ │
│ task_from_spec /    │  设 dispatch_type='auto'（默认）
│ _from_planning      │  设 dispatch_window_until = now() + N
└──────┬──────────────┘
       ▼
┌─────────────────────┐
│ tasks 表新增 pending  │
│ dispatch_type='auto'  │
│ window_until=now+10s  │
└──────┬──────────────┘
       ▼
   ┌───┴────┐
   │  10s  │  内（dispatch_window_until > now）
   │ 窗口期 │  ├─ 用户手动 dispatch → dispatch_type='manual', window=NULL
   │       │  └─ 用户不动 → window 倒计时归零
   └───┬────┘
       ▼
┌─────────────────────┐
│ Scheduler 每 10s 扫  │
│ WHERE pending AND    │
│   dispatch_type=auto │
│   AND (window IS NULL│
│        OR window<=now)│
└─────────────────────┘
       ▼
┌─────────────────────┐
│ 找到空闲节点 → assigned │
│ status=assigned        │
│ dispatch_type=auto     │
└─────────────────────┘
```

### 3.2 手动派发阻断自动派发

```
┌──────────────┐
│ 任务 pending  │
│ 窗口期 10s    │
└──────┬───────┘
       ▼ 用户在 TaskDetailDrawer 点"手动派发"，选 'code'
       ▼ POST /api/v1/tasks/{id}/dispatch  body: {target:'code'}
       ▼
┌────────────────────────────────────┐
│ task_service.dispatch_task          │
│  - execution_target = 'code'        │
│  - dispatch_type = 'manual'         │
│  - dispatch_window_until = NULL     │
│  - status = 'pending'               │
│  - assigned_node_id = NULL          │
│  - progress = 0                     │
└──────┬─────────────────────────────┘
       ▼
┌────────────────────────────────────┐
│ Scheduler 下一轮扫描                │
│  WHERE dispatch_type='auto'  ← 不再命中这条
│  ⇒ 这条任务永远不会被自动接管        │
└────────────────────────────────────┘
       ▼ 但 status=pending 还是会显示在列表
       ▼ 直到（理论上永远不会自动派发了）
```

> **注意**：手动 dispatch 后任务**不会立即被派发到节点**，仅做"标记"与"阻断"。真正的派发到节点需要等用户后续触发（暂未实现 MVP）；目前这个手动流程的实际价值是"标记此任务不走自动派发"，后续可扩展为"立即选节点派发"。

## 4. 降级链路

| 失败场景                                             | 降级行为                             | 日志事件                                                  |
| ------------------------------------------------ | -------------------------------- | ----------------------------------------------------- |
| 调度器扫描时任务刚过窗口期但已被手动 dispatch                      | 跳过（`dispatch_type='manual'` 过滤掉） | 无（按设计）                                                |
| 调度器扫描时窗口期已过但**没有空闲节点**                           | 不派发，等下一轮                         | `no_available_agent`（WARNING，附带 `dispatch_type=auto`） |
| 用户手动 dispatch 时任务不在 `pending` 状态                 | 沿用现有 `TaskDispatchError`（400）    | `task_dispatch_invalid_state`（ERROR）                  |
| 任务创建时 `dispatch_window_until` 落库失败（如 N+10s 解析失败） | Pydantic 校验在 Settings 层就拦住       | `invalid_dispatch_window_config`（ERROR，理论上不会出现）       |
| 调度器扫描耗时 > 10s 导致某些任务的窗口期实际已过但 SQL 漏扫             | 下一轮补扫（窗口期判断靠时间戳，与扫描耗时解耦）         | -                                                     |
| 手动 dispatch 的任务最终没有被任何路径消费（永远 pending）           | 列表里长期显示，需运维手动处理                  | 无（不属于本特性范围）                                           |

## 5. 接口契约

### 5.1 任务列表响应（`GET /api/v1/tasks`）

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "items": [
      {
        "id": "7d10893e-...",
        "title": "实现登录页",
        "agent_type": "code",
        "status": "running",
        "progress": 90,
        "assigned_node_id": "3f7bb90f-...",
        "assigned_node_name": "笔记本",        // ← 新增：JOIN agent_nodes.name
        "dispatch_type": "auto",                // ← 新增
        "dispatch_window_until": null,         // ← 新增（auto 派发后保留原始时间戳）
        "source": "spec_chat",
        ...
      }
    ],
    "pagination": {...}
  }
}
```

### 5.2 任务详情响应（`GET /api/v1/tasks/{id}`）

同上，3 个新字段均存在。

### 5.3 手动派发（`POST /api/v1/tasks/{id}/dispatch`）

**Request**：

```json
{ "target": "code", "mode": "build" }
```

**Response 200**：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "task_id": "7d10893e-...",
    "target": "code",
    "dispatch_type": "manual",        // ← 新增字段
    "status": "pending"
  }
}
```

## 6. 验收标准

| 编号    | 验收项                                                                                                          | 期望 |
| ----- | ------------------------------------------------------------------------------------------------------------ | -- |
| AC-1  | Alembic 迁移后 `dispatch_type` 列存在，CHECK 约束 `IN ('auto','manual')`                                              | ✔  |
| AC-2  | Alembic 迁移后 `dispatch_window_until` 列存在（nullable）                                                            | ✔  |
| AC-3  | Alembic 迁移后 `idx_tasks_dispatch_type` 复合索引存在                                                                 | ✔  |
| AC-4  | 任务创建时（spec / planning 两条路径）`dispatch_window_until = now() + 10s`                                             | ✔  |
| AC-5  | 调度器扫描时不命中 `dispatch_type='manual'` 的任务                                                                       | ✔  |
| AC-6  | 调度器扫描时不命中 `dispatch_window_until > now()` 的任务                                                                | ✔  |
| AC-7  | 手动 dispatch 后 `dispatch_type='manual'` + `dispatch_window_until=NULL`                                        | ✔  |
| AC-8  | 任务列表 `assigned_node_name` 字段在 `assigned_node_id` 存在时返回对应 `agent_nodes.name`                                  | ✔  |
| AC-9  | 任务列表 `assigned_node_name` 字段在 `assigned_node_id` 为 NULL 时返回 `null`（`outerjoin`）                              | ✔  |
| AC-10 | TaskTable 「分配节点」列显示 `assigned_node_name`（fallback `—`）                                                       | ✔  |
| AC-11 | TaskTable 「派发」列**始终**显示 `自动` / `手动` Tag                                                                      | ✔  |
| AC-12 | 任意 `status='pending' AND dispatch_type='auto'` 的任务可在 TaskDetailDrawer 手动 dispatch（不再限制 `source='spec_chat'`） | ✔  |
| AC-13 | `TASK_DISPATCH_WINDOW_SECONDS=0` 时任务创建后立即被自动派发（无窗口）                                                          | ✔  |
| AC-14 | `TASK_DISPATCH_WINDOW_SECONDS=60` 时任务有 1 分钟手动窗口                                                              | ✔  |
| AC-15 | 涉及接口按 logging-standards 输出 `task_manual_dispatched` / `task_auto_dispatched` 等事件                             | ✔  |
| AC-16 | ruff check + pytest 全过；前端 `npm run typecheck` 通过                                                             | ✔  |
| AC-17 | 端到端：E2E 脚本验证"创建任务 → 5 秒后手动 dispatch → 调度器不会接管"                                                               | ✔  |

## 7. 关联文档

* [ADR-017](../adr/017-task-dispatch-type-and-window.md)

* [017-task-dispatch-type-and-window-harness.md](./017-task-dispatch-type-and-window-harness.md)

* [002-central-dispatch-hub-spec.md](./002-central-dispatch-hub-spec.md)

* 相关规范：[api-standards.md](../standards/api-standards.md) / [database-standards.md](../standards/database-standards.md) / [backend-component-standards.md](../standards/backend-component-standards.md) / [frontend-component-standards.md](../standards/frontend-component-standards.md) / [logging-standards.md](../standards/logging-standards.md) / [environment-config-standards.md](../standards/environment-config-standards.md)

