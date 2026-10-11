# OPC 项目数据流设计

## 1. 设计目标

- 清晰描述任务从用户输入到最终交付的全链路数据流转。
- 明确中枢、Agent、数据库、缓存、文件存储之间的数据交互。
- 确保任务状态、节点状态、交付物状态一致可溯源。

## 2. 标准任务数据流

```
用户（前端）
    │
    │ POST /api/v1/tasks
    │ { title, description, requirements, priority }
    ▼
中央调度中枢（FastAPI）
    │
    ├──► PostgreSQL: 写入 tasks 表，status=pending
    │
    ├──► 需求解析器：提取 task_type、deliverables
    │
    ├──► 任务拆解引擎：生成 subtasks（串行/并行）
    │
    ├──► PostgreSQL: 写入 task_subtasks 表
    │
    └──► WebSocket: 向前端推送 task.created 事件
    │
    ▼
任务调度引擎
    │
    ├──► Redis: 查询在线空闲 Agent 节点
    │
    ├──► PostgreSQL: 查询 agent_nodes 表
    │
    └──► 选择最优节点
    │
    ▼
WebSocket: hub.task_dispatch → Agent 节点
    │
    ▼
Agent 节点
    │
    ├──► 下载资源：从 MinIO 读取 requirements/{task_id}/
    │
    ├──► 沙箱执行任务
    │
    ├──► WebSocket: agent.task_progress → 中枢
    │         中枢 → WebSocket: task.progress → 前端
    │         中枢 → Redis: 缓存进度
    │
    ├──► 上传交付物：MinIO deliverables/{task_id}/
    │
    └──► WebSocket: agent.task_result → 中枢
              中枢 → PostgreSQL: 更新 task 状态为 completed
              中枢 → PostgreSQL: 写入 task_deliverables
              中枢 → WebSocket: task.completed → 前端
    │
    ▼
结果聚合模块
    │
    ├──► PostgreSQL: 查询该任务全部子任务和交付物
    │
    ├──► MinIO: 校验交付物存在
    │
    └──► 生成完整交付包元数据
    │
    ▼
WebSocket + REST: 通知用户查看/下载交付物
```

## 3. 节点心跳数据流

```
Agent 节点（每 5~10 秒）
    │
    │ WebSocket: agent.heartbeat
    │ { client_id, status, current_task_id }
    ▼
中央调度中枢
    │
    ├──► Redis: 更新 heartbeat key（TTL 30 秒）
    │      key: opc:heartbeat:{client_id}
    │
    └──► PostgreSQL: 更新 agent_nodes.last_heartbeat_at
    │
    ▼
心跳检测任务（每 10 秒）
    │
    ├──► Redis: 扫描过期 heartbeat key
    │
    ├──► 标记节点 offline
    │
    └──► 触发任务迁移流程
```

## 4. 任务迁移数据流

```
心跳检测发现节点离线
    │
    ▼
中央调度中枢
    │
    ├──► PostgreSQL: 查询该节点上 running/assigned 任务
    │
    ├──► Redis: 查询同类型在线空闲节点
    │
    ├──► 选择新节点
    │
    ├──► PostgreSQL: 更新 task.agent_node_id
    │         status=assigned / 或重置为 pending
    │
    ├──► Redis: 将任务加入新节点队列
    │
    ├──► WebSocket: hub.task_dispatch → 新节点
    │
    ├──► PostgreSQL: 写入 task_logs（迁移记录）
    │
    └──► WebSocket: system.alert → 前端
```

## 5. 文件上传数据流

### 5.1 用户上传需求资料

```
前端
    │
    │ POST /api/v1/files/upload-url
    │ { filename, content_type, task_id }
    ▼
中央调度中枢
    │
    ├──► MinIO: 生成预签名上传 URL
    │
    └──► 返回 { url, key, expires_at }
    │
    ▼
前端
    │
    │ PUT 预签名 URL
    │ 文件二进制
    ▼
MinIO
    │
    ▼
前端通知中枢上传完成
    │
    ▼
中央调度中枢
    │
    ├──► PostgreSQL: 写入 file_records 表
    │
    └──► 关联到 task.input_payload
```

### 5.2 Agent 上传交付物

```
Agent 节点
    │
    ├──► 生成交付物文件
    │
    ├──► MinIO: 上传文件到 deliverables/{task_id}/{filename}
    │
    ├──► WebSocket: agent.task_result → 中枢
    │         包含 deliverables 元数据
    │
    ▼
中央调度中枢
    │
    ├──► PostgreSQL: 写入 task_deliverables 表
    │
    └──► PostgreSQL: 更新 task 状态为 completed
```

## 6. 任务失败重试数据流

```
Agent 节点上报 task_failed
    │
    ▼
中央调度中枢
    │
    ├──► PostgreSQL: 更新 task 状态为 failed
    │
    ├──► 检查 retry_count < 1
    │
    ├──► 是 → retry_count += 1
    │         重新调度 / 迁移到同类型节点
    │         WebSocket: hub.task_dispatch → 新节点
    │
    └──► 否 → 冻结任务
              WebSocket: task.failed → 前端
              推送人工核查通知
```

## 7. 数据一致性保障

| 数据 | 主存储 | 缓存 | 一致性策略 |
|------|--------|------|-----------|
| 任务状态 | PostgreSQL | Redis（进度） | 以 PostgreSQL 为准，Redis 用于实时查询 |
| 节点状态 | PostgreSQL | Redis（心跳） | 心跳 TTL 过期后同步到 PostgreSQL |
| 任务队列 | Redis List/ZSet | - | 持久化到 PostgreSQL 任务表 |
| 交付物元数据 | PostgreSQL | - | MinIO 为真实存储，PG 记录元数据 |

## 8. 关键数据模型

### 8.1 tasks 表关键字段

| 字段 | 说明 |
|------|------|
| id | 任务 UUID |
| title | 任务标题 |
| status | 任务状态 |
| priority | 优先级 |
| agent_node_id | 分配节点 ID |
| input_payload | 输入参数（jsonb） |
| output_payload | 输出结果（jsonb） |
| retry_count | 重试次数 |

### 8.2 agent_nodes 表关键字段

| 字段 | 说明 |
|------|------|
| id | 节点 UUID |
| client_id | 节点唯一标识 |
| agent_type | 节点类型 |
| status | 在线/离线/空闲/忙碌 |
| capabilities | 能力标签（jsonb） |
| last_heartbeat_at | 最后心跳时间 |

### 8.3 task_deliverables 表关键字段

| 字段 | 说明 |
|------|------|
| id | UUID |
| task_id | 关联任务 |
| subtask_id | 关联子任务 |
| type | 交付物类型 |
| storage_path | MinIO 路径 |
| size_bytes | 文件大小 |

## 9. 关联文档

- [项目设计文档](../OPC超级个体%20·%20AI员工智能体协作平台｜项目设计文档（正式版）.md)
- [002-central-dispatch-hub-spec.md](../spec/002-central-dispatch-hub-spec.md)
- [004-agent-node-protocol-spec.md](../spec/004-agent-node-protocol-spec.md)
- [003-task-node-state-spec.md](../spec/003-task-node-state-spec.md)
- [005-file-storage-spec.md](../spec/005-file-storage-spec.md)
- [database-standards.md](../standards/database-standards.md)
