# OPC 项目数据库规范

## 1. 选型

- **主数据库**：PostgreSQL 15+
- **缓存/队列/状态**：Redis 7+
- **ORM / 模型**：SQLAlchemy 2.x + Alembic（迁移）

## 2. 命名规范

### 2.1 表名

- 复数形式。
- 小写 + 下划线分隔。
- 避免 PostgreSQL 保留字。

| 表名 | 说明 |
|------|------|
| `tasks` | 任务表 |
| `task_subtasks` | 子任务表 |
| `agent_nodes` | Agent 节点表 |
| `task_logs` | 任务日志表 |
| `task_deliverables` | 任务交付物表 |
| `system_alerts` | 系统告警表 |

### 2.2 字段名

- 小写 + 下划线分隔。
- 主键统一用 `id`（UUID 或 bigint）。
- 外键命名为 `关联表单数_id`。
- 时间字段统一为 `created_at` / `updated_at`。

```sql
-- 推荐
create table tasks (
    id uuid primary key default gen_random_uuid(),
    title varchar(255) not null,
    status varchar(32) not null,
    agent_node_id uuid references agent_nodes(id),
    created_at timestamp with time zone not null default now(),
    updated_at timestamp with time zone not null default now()
);
```

### 2.3 索引命名

| 类型 | 命名格式 | 示例 |
|------|---------|------|
| 普通索引 | `idx_表名_字段名` | `idx_tasks_status` |
| 联合索引 | `idx_表名_字段1_字段2` | `idx_tasks_status_agent_node_id` |
| 唯一索引 | `uk_表名_字段名` | `uk_agent_nodes_client_id` |
| 外键约束 | `fk_表名_关联表_字段` | `fk_tasks_agent_nodes_agent_node_id` |

### 2.4 约束命名与变更规范

- **所有约束必须显式命名**。`CheckConstraint`、`UniqueConstraint`、`ForeignKeyConstraint` 必须在 SQLAlchemy 模型中显式指定 `name=`，禁止依赖 SQLAlchemy 自动生成（如 `chk_agent_nodes_status`）。
- 状态/枚举字段的 CHECK 约束命名统一为 `status` / `chk_表名_状态字段`，与代码枚举对应。
- **修改 CHECK 约束前**，必须先用 SQL 查询数据库中实际存在的约束名（`pg_constraint` / `information_schema.table_constraints`）。
- **删除旧约束要兜底**。迁移脚本中应使用 `DROP CONSTRAINT IF EXISTS` 同时删除历史上可能使用过的约束名，避免旧约束残留导致新值非法。
- **迁移后必须验证**。升级/降级后查询 `pg_constraint`，确认旧约束已清理、新约束已生效。

```sql
-- 修改状态约束前，先查询实际约束名
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'agent_nodes'::regclass AND contype = 'c';

-- 迁移脚本示例：同时删除新旧约束名再重建
ALTER TABLE agent_nodes DROP CONSTRAINT IF EXISTS chk_agent_nodes_status;
ALTER TABLE agent_nodes DROP CONSTRAINT IF EXISTS status;
ALTER TABLE agent_nodes ADD CONSTRAINT status
    CHECK (status IN ('offline', 'starting', 'idle', 'busy', 'error'));
```

## 3. 字段设计规范

### 3.1 主键

- 推荐 UUID v7 或 bigint 自增。
- 不推荐使用业务含义主键。
- OPC 项目推荐 UUID，便于分布式节点生成 ID 不冲突。

### 3.2 时间字段

- 统一使用 `timestamp with time zone`（PostgreSQL 中的 `timestamptz`）。
- 不要存本地时间。

### 3.3 枚举字段

- 状态类字段使用 `varchar` + CHECK 约束，不直接用 PostgreSQL ENUM（便于扩展）。
- 或者使用 smallint + 应用层映射（性能优先时）。

```sql
status varchar(32) not null check (status in ('pending', 'assigned', 'running', 'completed', 'failed', 'timeout', 'cancelled'))
```

### 3.4 JSON 字段

- 动态配置、扩展属性使用 `jsonb`。
- 必须对常用查询路径加 GIN 索引。

```sql
metadata jsonb default '{}'::jsonb,
constraint chk_task_metadata check (jsonb_typeof(metadata) = 'object')
```

## 4. 迁移规范

- 所有表结构变更必须通过 Alembic 迁移脚本。
- 迁移脚本命名：`{版本号}_描述.py`，如 `20260803_01_create_tasks_table.py`。
- 禁止手动修改生产数据库。
- 迁移脚本必须可回滚（downgrade）。

## 5. SQL 编写规范

- 关键字大写，表名/字段名小写。
- 多行 SQL 使用格式化工具（如 sqlfluff）。
- 禁止使用 `SELECT *`，必须显式列名。
- 大表查询必须带 LIMIT 或分页。
- 避免 N+1 查询，使用 JOIN 或批量查询。

```sql
-- 推荐
SELECT t.id, t.title, t.status, an.client_id
FROM tasks t
LEFT JOIN agent_nodes an ON t.agent_node_id = an.id
WHERE t.status = 'running'
LIMIT 100;
```

## 6. 核心表设计参考

### 6.1 tasks（任务表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | uuid | 主键 |
| title | varchar(255) | 任务标题 |
| description | text | 任务描述 |
| status | varchar(32) | 任务状态 |
| priority | varchar(16) | 优先级 |
| agent_node_id | uuid | 分配节点 ID |
| parent_task_id | uuid | 父任务 ID |
| input_payload | jsonb | 输入参数 |
| output_payload | jsonb | 输出结果 |
| started_at | timestamptz | 开始时间 |
| completed_at | timestamptz | 完成时间 |
| created_at | timestamptz | 创建时间 |
| updated_at | timestamptz | 更新时间 |

### 6.2 agent_nodes（Agent 节点表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | uuid | 主键 |
| client_id | varchar(128) | 节点唯一标识 |
| agent_type | varchar(32) | 节点类型：programming/video/copywriting 等 |
| status | varchar(32) | 在线/离线/忙碌/空闲 |
| capabilities | jsonb | 能力标签 |
| hardware_info | jsonb | 硬件配置 |
| last_heartbeat_at | timestamptz | 最后心跳时间 |
| registered_at | timestamptz | 注册时间 |

## 7. Redis 使用规范

- 任务队列：`opc:queue:pending:{agent_type}`
- 节点心跳：`opc:heartbeat:{client_id}`
- 任务进度缓存：`opc:task:progress:{task_id}`
- 分布式锁：`opc:lock:{resource}`
- 所有 key 必须加 `opc:` 前缀，避免冲突。
- 设置合理过期时间，避免 key 堆积。

## 8. 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-08-03 | 初始创建 | AI助手 |
