# ADR-017：任务派发模式（手动 / 自动）+ 分配节点显示名称

## 状态

Accepted

## 日期

2026-08-26

## 问题背景

2026-08-26 巡检发现任务队列页（`TaskQueue` / `TaskTable`）两个体验问题：

1. **「分配节点」列显示节点 ID 截断**（如 `3f7bb90f-05eb-49cc-8661-9d9e81b6d69c` 截前 8 位 `3f7bb90f`），运维看到的是后端 UUID，不是节点管理页里可读的节点名（`笔记本` / `r410` / `localhost.localdomain-programming-j1169i`）。两个页面信息不对齐，运维需要在「节点管理」页里手查 client\_id。
2. **「派发」列固定为** **`—`**：当前实现只在 `source='spec_chat' && status='pending'` 时显示派发 `Select`，否则显示 `—`，无法判断"这个任务是用户手动派发的、还是中枢自动派发的"。
3. **「手动派发」功能在 DB 层未落地**：`POST /tasks/{id}/dispatch` 只写 `execution_target` 字段做记录，**没有**改派发模式 / **没有**阻断自动调度器接管。任务创建后 10 秒内没手动 dispatch，调度器照样自动接管。换言之，**用户以为"10 秒内手动选择才有效"，但代码里这个倒计时窗口并不存在**。

### 根因

* 任务表 `tasks` 没有 `dispatch_type` 字段记录派发模式；

* 任务表没有 `dispatch_window_until` 字段记录"手动派发窗口期";

* `scheduler.dispatch_pending_tasks()` 无差别扫描所有 `pending` 任务，不区分窗口期；

* 任务列表 API 不 JOIN `agent_nodes.name`，只返回 `assigned_node_id`。

### 需求

1. `tasks` 表新增 `dispatch_type` 字段（`auto` / `manual`），前端可读。
2. `tasks` 表新增 `dispatch_window_until` 字段（`TIMESTAMPTZ`），任务创建时设 `now() + N` 秒，**调度器只接管窗口已过的任务**。
3. `POST /tasks/{id}/dispatch` 手动派发时写 `dispatch_type='manual'` + 清空 `dispatch_window_until`，**真正阻断**自动派发。
4. 任务列表接口 `GET /tasks` JOIN `agent_nodes.name`，响应里加 `assigned_node_name` 字段。
5. 任务列表 `TaskTable` 「分配节点」列显示 `assigned_node_name`（fallback `—`）。
6. 任务列表 `TaskTable` 「派发」列**始终**显示 `自动` / `手动` 标签（不再受 `source=spec_chat && status=pending` 限制）。
7. 窗口秒数可配置：`TASK_DISPATCH_WINDOW_SECONDS`（默认 10，最小 0；0 表示"无窗口，pending 任务下一轮自动派发"）。
8. 手动派发入口**不限制 source**（任何 `status='pending' AND dispatch_type='auto'` 的任务都可手动派发）。

## 决策

### 1. 任务表新增 `dispatch_type` 字段

* **类型**：`VARCHAR(16) NOT NULL DEFAULT 'auto'`。

* **CHECK 约束**：`dispatch_type IN ('auto', 'manual')`。

* **索引**：`CREATE INDEX idx_tasks_dispatch_type ON tasks (dispatch_type, dispatch_window_until)` 复合索引，加速调度器扫描。

```sql
ALTER TABLE tasks
    ADD COLUMN dispatch_type VARCHAR(16) NOT NULL DEFAULT 'auto',
    ADD CONSTRAINT dispatch_type CHECK (dispatch_type IN ('auto', 'manual')),
    ADD COLUMN dispatch_window_until TIMESTAMPTZ NULL;
```

* 迁移期间不重写存量行（`DEFAULT 'auto'` 自动生效；`dispatch_window_until` 由 `NULL` 起步，调度器将所有 NULL 视为"窗口已过"）。

### 2. 调度器扫描条件收紧

`scheduler.dispatch_pending_tasks()` 改为：

```python
now = datetime.now(UTC)
result = await session.execute(
    select(Task)
    .where(
        Task.status == TaskStatus.PENDING.value,
        Task.dispatch_type == "auto",                       # 手动已锁的不会再被自动接管
        or_(                                           # 窗口已过 或 窗口未设（NULL）
            Task.dispatch_window_until.is_(None),
            Task.dispatch_window_until <= now,
        ),
    )
    .order_by(Task.created_at.asc())
    .with_for_update(skip_locked=True),                  # 已有 skip_locked：避免与并发 task_progress 抢锁
)
```

### 3. 任务创建时设 `dispatch_window_until`

`task_service.create_task` 统一在落库前注入：

```python
window_seconds = get_settings().task_dispatch_window_seconds
window_until = datetime.now(UTC) + timedelta(seconds=window_seconds)
# 三个 create_* 路径（spec / planning / dispatch 重置）都设
task = Task(..., dispatch_window_until=window_until)
```

### 4. 手动派发接口

`task_service.dispatch_task` 改为：

```python
task.execution_target = target
if mode is not None:
    task.execution_mode = mode
task.dispatch_type = "manual"        # ← 阻断自动派发
task.dispatch_window_until = None    # ← 关闭窗口
task.status = TaskStatus.PENDING.value
task.assigned_node_id = None
task.progress = 0
```

并允许任何 `source` 的任务进入此路径（去掉 `source='spec_chat'` 限制）。

### 5. 任务列表 JOIN agent\_nodes.name

`task_service.list_tasks` 改为：

```python
stmt = (
    select(Task, AgentNode.name)
    .outerjoin(AgentNode, AgentNode.id == Task.assigned_node_id)
    .order_by(Task.created_at.desc())
)
...
# 构造响应时填 assigned_node_name
```

`TaskResponse` 新增 `dispatch_type: str` / `dispatch_window_until: datetime | None` / `assigned_node_name: str | None` 三字段。

### 6. Pydantic Settings：任务派发窗口

`app/core/config.py` 新增：

```python
task_dispatch_window_seconds: int = Field(
    default=10,
    ge=0,
    alias="TASK_DISPATCH_WINDOW_SECONDS",
    description="任务手动派发窗口秒数；0 = 无窗口",
)
```

`ge=0`（不是 `ge=60`）—— 因为 0 是合法配置（运维想"立即自动派发"也合理）。

### 7. 前端 TaskTable 改造

* 删掉 `SUBSYSTEM_OPTIONS` 与 `dispatching` 状态对"派发列"的渲染控制；

* 「分配节点」列改为渲染 `assigned_node_name`（fallback `—`），保留 `title` 显示节点 ID（hover 用，方便调试）；

* 「派发」列**始终**显示 `Tag`：

  * `auto` → 蓝色 Tag "自动"

  * `manual` → 绿色 Tag "手动"

* 保留"可手动派发"逻辑：点击 Tag **或** 操作列新增 "手动派发" 按钮时调 `dispatchTask`；窗口已过的任务仍允许手动 dispatch（用于"重新派发"场景）。

* 简化：MVP 阶段派发列**只读**展示，不在列表里直接改。要手动派发的入口放在 `TaskDetailDrawer`（任务详情抽屉）的"操作"区。

* 改后**无 Select / 无 onDispatch 回调**，`TaskQueue.tsx` 同步删除 `dispatching` 状态与 `handleDispatch` 函数。

### 8. 接口日志规范

所有新增 / 修改接口按 `logging-standards.md` §4 落结构化日志：

| 事件                             | 触发点                              | 必填字段                                              |
| ------------------------------ | -------------------------------- | ------------------------------------------------- |
| `task_created` 增字段             | `create_task`                    | `dispatch_type=auto` `dispatch_window_until`      |
| `task_manual_dispatched`       | `dispatch_task`                  | `task_id` `target` `dispatch_type=manual`         |
| `task_auto_dispatched`         | `scheduler._assign_task_to_node` | `task_id` `assigned_node_id` `dispatch_type=auto` |
| `task_dispatch_window_expired` | `scheduler` 扫描时发现窗口已过            | `task_id` `dispatch_window_until`（DEBUG）          |

`task_dispatched`（原事件名）保留，附加 `dispatch_type` 字段。

## 备选方案

| 方案                                                                           | 优势                       | 劣势                                     | 结论                    |
| ---------------------------------------------------------------------------- | ------------------------ | -------------------------------------- | --------------------- |
| A（本决策）：tasks 加 `dispatch_type` + `dispatch_window_until` + 调度器过滤             | 业务语义清晰，DB 层可查；窗口可配；测试覆盖全 | 多 2 列 + 1 配置项                          | ✅ 选中                  |
| B：仅加 `dispatch_type` 字段（不改调度器逻辑）                                             | 改动最小                     | "手动派发"仍被调度器接管，**用户期望的 10 秒倒计时窗口不存在**   | ❌ 否决（2026-08-26 用户反馈） |
| C：把 `execution_target` 升级为 `assigned_node_id` 并加 `dispatch_type='manual'` 标记 | 复用现有字段                   | `execution_target` 是子系统字符串，不是节点；混用语义不清 | ❌ 否决                  |
| D：窗口写死 10 秒，不加配置项                                                            | 简单                       | 后期要调窗口要改代码；不满足用户"可配"要求                 | ❌ 否决                  |
| E：手动派发保留 source=spec\_chat 限制                                                | 减少 UI 改动                 | 与用户期望的"任何 pending 都可手动"不符              | ❌ 否决（2026-08-26 用户反馈） |
| F：派发列保持可交互 Select（不是只读 Tag）                                                  | 列表行内可改                   | 易误触；不利于快速浏览                            | ❌ 否决                  |

## 决策原因

1. **真实落地 10 秒倒计时**：用户原以为"手动派发"是有效的，但代码上并没有；这是用户体验 vs 实际行为的偏差，必须修复。
2. **DB 层可追溯**：`dispatch_type` + `dispatch_window_until` 让运维 / 排障能从 SQL 直接看每个任务的派发历史。
3. **窗口可配**：0\~N 任意值，方便不同环境调优（生产 60 秒，dev 0 秒）。
4. **不限制 source**：用户已确认此为期望行为，避免业务被早期 MVP 的 UI 限制绑死。
5. **结构化日志**：沿用 `logging-standards.md`，便于后续加 dashboard 统计「每日手动派发占比」等指标。

## 影响

### 对系统架构的影响

* `tasks` 表加 2 列 + 1 索引 + 1 CHECK 约束。

* 调度器扫描 SQL 调整（加 `dispatch_type` + `dispatch_window_until` 过滤），仍走 `FOR UPDATE SKIP LOCKED`。

* 任务列表 API 响应加 3 字段；JOIN 一次 `agent_nodes`，无额外 N+1。

* 任务列表 API 走 `outerjoin`：未分配节点（`assigned_node_id IS NULL`）仍能返回 `assigned_node_name=None`。

### 对开发的影响

* **后端**：

  * `app/core/config.py` 加 `task_dispatch_window_seconds`。

  * `app/models/task.py` 加 `dispatch_type` / `dispatch_window_until` 字段与 CHECK 约束、复合索引。

  * `app/schemas/task.py` `TaskResponse` 加 3 字段。

  * `app/services/task_service.py`：

    * `create_task` / `_create_task_from_spec` / `_create_task_from_planning` 设 `dispatch_window_until`。

    * `dispatch_task` 写 `dispatch_type='manual'` + `dispatch_window_until=None`；去 `source` 限制。

    * `list_tasks` 改用 `select(Task, AgentNode.name).outerjoin(...)`。

  * `app/services/scheduler.py`：

    * `dispatch_pending_tasks` 加 `dispatch_type='auto'` 与窗口过滤。

    * `_assign_task_to_node` 显式写 `dispatch_type='auto'`（虽然默认就是 auto，防御性）。

  * `app/api/v1/endpoints/tasks.py`：dispatch 接口日志加 `dispatch_type` 字段。

* **Alembic 迁移**：`20260827000000_add_dispatch_type_and_window_to_tasks.py`。

* **前端**：

  * `src/types/task.ts` `Task` 加 3 字段。

  * `src/components/TaskTable.tsx` 改两列渲染。

  * `src/pages/TaskQueue.tsx` 删 `dispatching` 状态与 `handleDispatch` 函数。

  * `src/components/TaskDetailDrawer.tsx` 加"手动派发"按钮（如有需要）。

* **Agent 端 / 前端 API client**：无变化（这是纯中枢层逻辑）。

### 对安全的影响

* 派发模式字段不涉及鉴权改动；与 ADR-016 一致，MVP 阶段不引入 RBAC。

### 对运维的影响

* 升级后**存量任务** `dispatch_type` 默认 `auto`、`dispatch_window_until=NULL`（迁移会执行 `UPDATE ... SET dispatch_window_until = NULL`，与默认值一致）。

* 存量 `pending` 任务会被调度器**立即**接管（窗口为 NULL）—— 这是预期的：升级后没有"老任务可手动派发"的概念，避免遗留下 pending 任务。

* 运维可调 `TASK_DISPATCH_WINDOW_SECONDS`：

  * 0：禁用手动窗口（pending 任务立即被自动派发，保留旧行为）

  * 10（默认）：10 秒手动窗口

  * 60：1 分钟手动窗口（适合人工介入多的场景）

* 新增"每日手动派发占比"日志埋点（`task_manual_dispatched` / `task_auto_dispatched`），便于后续做统计。

## 关联文档

* [ADR-015 任务卡死检测与自动失败兜底](./015-task-stale-detector.md)

* [ADR-016 节点显示名自定义 + Agent 类型更名为](./016-agent-node-display-name-and-code-type.md) [`code`](./016-agent-node-display-name-and-code-type.md)

* [015-task-stale-detector-spec.md](../spec/015-task-stale-detector-spec.md)

* [002-central-dispatch-hub-spec.md](../spec/002-central-dispatch-hub-spec.md)

* 相关规范：[api-standards.md](../standards/api-standards.md) / [database-standards.md](../standards/database-standards.md) / [backend-component-standards.md](../standards/backend-component-standards.md) / [frontend-component-standards.md](../standards/frontend-component-standards.md) / [logging-standards.md](../standards/logging-standards.md) / [environment-config-standards.md](../standards/environment-config-standards.md)

## 实际实施情况

### 2026-08-26 实施

- **数据库迁移**：`alembic/versions/20260827000000_add_dispatch_type_and_window_to_tasks.py` 已执行。
  - 真实 PG 验证：`dispatch_type` 列（VARCHAR(16) NOT NULL DEFAULT 'auto'）+ `dispatch_window_until`（TIMESTAMPTZ nullable）创建成功；`idx_tasks_dispatch_type (dispatch_type, dispatch_window_until)` 复合索引创建成功；`chk_tasks_dispatch_type` CHECK 约束生效。
  - 41 条存量任务全部回填 `dispatch_type='auto'`、`dispatch_window_until=NULL`（依赖 server_default）。
- **Pydantic Settings**：`task_dispatch_window_seconds` 字段（默认 10，`ge=0`）已加入 `app/core/config.py`。
- **后端 ORM**：`Task` 模型新增 `dispatch_type` / `dispatch_window_until` 字段及 CHECK 约束、复合索引；`TaskStatus` 状态语义未变。
- **后端 Schema**：`TaskResponse` 加 3 字段（`assigned_node_name` / `dispatch_type` / `dispatch_window_until`）；`TaskDispatchResponse` 加 `dispatch_type` 字段。
- **后端 Service**：
  - `_create_task_from_spec` / `_create_task_from_planning` 显式注入 `dispatch_type='auto'` + `dispatch_window_until=now() + TASK_DISPATCH_WINDOW_SECONDS`。
  - `dispatch_task` 写 `dispatch_type='manual'` + 清空 `dispatch_window_until`；拒绝重复手动派发（已是 manual 时抛 `TaskDispatchError`）。
  - `list_tasks` 改用 `select(Task, AgentNode.name).outerjoin(...)` 并通过 `_build_task_response` 注入 `assigned_node_name`（已分配时为节点名，未分配时为 NULL）。
- **后端 Scheduler**：`dispatch_pending_tasks` 加 `dispatch_type='auto' AND (window IS NULL OR window <= now())` 过滤；`_assign_task_to_node` 显式保留 `dispatch_type='auto'` + 清空 `dispatch_window_until`。
- **后端日志**：
  - `task_created` 增字段 `dispatch_type` / `dispatch_window_until`
  - `task_manual_dispatched` 替代 `task_dispatched`（派发模式 = manual）
  - `task_auto_dispatched` 替代 `task_dispatched`（派发模式 = auto）
- **测试**：
  - 新增 `tests/api/v1/test_task_dispatch_type_and_window.py` 共 15 项：Service 7 项（创建/默认/manual/重置/拒绝/race）+ Scheduler 2 项 + HTTP 契约 3 项 + Idempotent 1 项 + 404 1 项 + 拒绝重复 1 项。
  - `conftest.py` 调整：`_ensure_test_schema` autouse session fixture 改用 `alembic upgrade head`（比 `Base.metadata.create_all` 可靠，避免老表缺新列）。
  - `tests/api/v1/test_agent_nodes.py` `test_register_agent_node_empty_client_id_creates_new` 调整：硬编码 `programming` 改为 `code`，并循环用不同 name（避开 ADR-016 partial unique index）。
- **前端**：
  - `src/types/task.ts` `Task` 加 `assigned_node_name?` / `dispatch_type?` / `dispatch_window_until?` 三字段（均 optional，兼容后端可能不返回的场景）。
  - `src/api/tasks.ts` `TaskDispatchResponse` 加 `dispatch_type?`。
  - `src/components/TaskTable.tsx` 重写：删 `dispatching` / `onDispatch` props，删 SUBSYSTEM_OPTIONS；「分配节点」列渲染 `assigned_node_name`（fallback `—`，title 显示 UUID 便于调试）；「派发」列**始终**显示 `auto` / `manual` Tag（不再受 source/spec_chat/pending 限制）。
  - `src/components/TaskDetailDrawer.tsx` 重写：加「派发模式」行 + 「分配节点」行 + 抽屉 footer 加「手动派发」按钮（仅当 `dispatch_type='auto' && status='pending'` 时显示）；加新 Modal 用于手动派发确认；eslint-disable 局部禁用 react-hooks/set-state-in-effect（Drawer 状态同步的标准 React 模式，无更优替代）。
  - `src/pages/TaskQueue.tsx` 简化：删 `dispatching` 状态与 `handleDispatch` 函数；副标题改为"任务创建后默认进入自动派发队列；如需手动派发，请在任务详情抽屉操作"。

### 全量回归

| 项 | 结果 |
|---|---|
| `ruff check`（中枢我改的 6 个文件 + 1 迁移） | All checks passed |
| 中枢后端 pytest | **174 passed** in 73.74s（其中 15 项为本次新增） |
| Agent 端 pytest | **74 passed** in 4.26s（无新增、无回归） |
| 前端 `tsc -b` | 0 errors |
| 前端我改的 5 个文件 `eslint` | 干净 |
| Alembic `upgrade 20260827_task_dispatch` 真实 PG | 成功，41 行 `dispatch_type='auto'`，2 行 idx + 1 个 chk 约束 |
| 真实数据 sanity：当前中枢 41 个任务全部有 `dispatch_type='auto'` | ✔ |

## 变更记录

| 日期         | 变更内容                                                                                                                        | 变更人  |
| ---------- | --------------------------------------------------------------------------------------------------------------------------- | ---- |
| 2026-08-26 | 初始创建：任务表加 `dispatch_type` / `dispatch_window_until` + 调度器窗口过滤 + `TASK_DISPATCH_WINDOW_SECONDS` 可配 + 任务列表 JOIN 节点名 + 前端两列重渲染 | AI助手 |
| 2026-09-11 | 修正"已是 manual 一律拒绝重复派发"规则：pending+manual 是 plan 批准后的合法等待态（调度器不接管），拒绝再派发会导致任务永久卡死待分配。改为仅拦截在途状态（running/assigned）；前端手动派发按钮条件同步放宽为 `pending \|\| failed` | AI助手 |

