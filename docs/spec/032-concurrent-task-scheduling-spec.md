# 节点并发任务调度 Spec（资源水位预筛 + Agent 自评拒绝 + 改派等待）

> 关联决策：[ADR-032](../adr/032-concurrent-task-scheduling.md)
> Harness：[032-concurrent-task-scheduling-harness.md](./032-concurrent-task-scheduling-harness.md)
> Changes：[032-concurrent-task-scheduling-changes.md](./032-concurrent-task-scheduling-changes.md)

## 1. 功能描述

将任务调度从「节点单任务互斥（idle/busy 状态机）」改为「资源水位 + 并发槽位」
双层判定：

- **中枢**按节点心跳上报的真实资源占用（ADR-014 metrics）与在跑任务数预筛
  候选节点，不再以 `busy` 状态排除节点。
- **Agent** 接收派单时本地自评（槽位、实时资源），不满足则拒绝并携带原因。
- 被拒绝的任务回 `pending` 由中枢改派其他节点；全部节点暂不可用时任务等待，
  节点释放资源后自动派发。

## 2. 用户故事

- US-1：作为平台用户，我希望一个资源充裕的节点能同时执行多个任务，而不是
  一个任务占住整台节点、其余任务长时间排队。
- US-2：作为平台用户，我希望节点资源（CPU/内存）紧张时新任务自动分流到
  其他节点或排队等待，而不是硬塞给繁忙节点导致全部任务变慢/失败。
- US-3：作为运维者，我希望拒绝原因可追溯（槽位满 / 资源忙），便于判断
  是否需要扩容节点或调高并发上限。

## 3. 核心流程

### 3.1 自动调度流程（改造后）

```
调度循环（10s 周期，现有）
  │
  ▼
扫描 pending 任务（dispatch_type=auto 且过派发窗口）
  │
  ▼
候选节点预筛（新逻辑）：
  ① 在线：last_heartbeat_at 在心跳超时窗口内
  ② 资源水位：metrics.cpu.percent < CPU阈值(默认85)
              且 metrics.memory.percent < 内存阈值(默认85)
              （无 metrics 数据 → 仅受槽位限制，放行）
  ③ 并发槽位：在跑任务数(assigned/running 计数) < node.max_concurrent_tasks(默认2)
  ④ 回避集合：任务本轮未被该节点拒绝过（进程内 TTL 记录）
  │
  ▼ 按综合负载分 load 升序取第 1 名（行锁 FOR UPDATE）
分配：task → assigned，指派 node；节点 status 不再强制置 busy
  │
  ▼ WS 下发 hub.task_dispatch
Agent 本地自评：
  ├─ 在跑数 >= AGENT_MAX_CONCURRENT_TASKS(默认2) → rejected(slots_full)
  ├─ 实时 CPU/内存 >= 90%（Agent 侧阈值）       → rejected(resource_busy)
  └─ 通过 → accepted，任务并发执行（每任务独立 SSE consumer）
  │
  ▼ 若 rejected
中枢：任务回 pending，记录「任务→拒绝节点」回避（5 分钟 TTL 或改派成功清除），
      下轮调度自动改派其他候选节点；无候选则保持 pending 等待
```

### 3.2 等待再分配

- 所有节点不可用时任务保持 `pending`，不新增等待队列——调度循环持续扫描，
  节点任务完成（槽位释放）或资源水位回落后自然派发。
- 手动派发（dispatch_type=manual）同样走候选预筛；用户显式指定节点时
  跳过资源预筛与槽位检查（人工决策优先），仅依赖 Agent 自评兜底。

### 3.3 节点状态语义

| 状态 | 语义（新） | 调度是否可选 |
|------|-----------|-------------|
| idle | 在线且在跑任务数 = 0 | ✅（需过资源预筛） |
| busy | 在线且在跑任务数 > 0（纯展示态） | ✅（需过资源预筛与槽位检查） |
| offline | 心跳超时/未注册 | ❌ |
| error | 节点异常 | ❌ |

## 4. 协议变更

### 4.1 `agent.task_rejected`（上行，payload 扩展）

```json
{
  "event": "agent.task_rejected",
  "payload": {
    "task_id": "...",
    "reason": "slots_full | resource_busy | bridge_not_ready | shutting_down",
    "message": "可选人类可读说明"
  }
}
```

- `reason` 可选；旧 Agent 缺省时中枢按 `slots_full` 处理。
- 中枢处理：任务回 `pending` + 回避记录；**不再**将节点置 idle
  （节点状态由心跳与在跑数派生）。

### 4.2 `agent.heartbeat`（上行，payload 扩展）

新增可选 `running_task_count: int`（当前在跑任务数），用于展示与审计对账。

### 4.3 配置项

中枢（Pydantic Settings，.env.example 同步）：

| 配置 | 默认 | 说明 |
|------|------|------|
| `DISPATCH_CPU_THRESHOLD_PERCENT` | 85 | 调度预筛 CPU 使用率上限（%） |
| `DISPATCH_MEM_THRESHOLD_PERCENT` | 85 | 调度预筛内存使用率上限（%） |

Agent（opc-agent-programming `.env.example` 同步）：

| 配置 | 默认 | 说明 |
|------|------|------|
| `AGENT_MAX_CONCURRENT_TASKS` | 2 | 节点本地最大并发执行任务数 |
| `AGENT_BUSY_CPU_THRESHOLD_PERCENT` | 90 | 本地自评 CPU 拒绝阈值（%） |
| `AGENT_BUSY_MEM_THRESHOLD_PERCENT` | 90 | 本地自评内存拒绝阈值（%） |

数据库：`agent_nodes` 新增 `max_concurrent_tasks INTEGER NOT NULL DEFAULT 2`
（Alembic 迁移）。

## 5. 降级链路

| 异常场景 | 降级策略 |
|---------|---------|
| 节点无 metrics 数据（旧 Agent/心跳缺失） | 资源预筛放行，仅受槽位上限与 Agent 自评约束 |
| 回避记录膨胀 | 5 分钟 TTL + 任务改派成功即清除；进程重启自然清空 |
| 全部节点持续不可用 | 任务保持 pending，前端任务列表展示「排队中」现状不变 |
| 新旧版本混部 | 旧 Agent 拒绝（无 reason）按 slots_full 处理，改派/等待行为不变差 |
| Agent 并发执行时 tilacode 崩溃 | 该节点所有在跑任务按 failed 上报（现状扩展为多任务），中枢可重新派发 |

## 6. 验收标准

- [ ] 单节点资源空闲时可同时执行 2 个任务（两个任务均 assigned→running→completed）。
- [ ] 在跑任务数达到上限的节点不再被派新任务；任务改派到其他节点或保持 pending。
- [ ] 心跳 metrics 中 CPU/内存超阈值的节点不被调度选中。
- [ ] Agent 槽位满时回复 `task_rejected(reason=slots_full)`，中枢将任务回 pending 并改派。
- [ ] 被拒绝任务不会被同一节点立即反复拒绝（回避机制生效）。
- [ ] 节点 `busy` 状态不再阻止调度；任务完成后节点在跑数减 1。
- [ ] 全部改动接口按 logging-standards 输出结构化日志（dispatch_precheck /
      task_rejected_received / task_redispatched 等事件）。

## 7. 关联文档

- [ADR-032](../adr/032-concurrent-task-scheduling.md)
- [003-task-node-state-spec.md](./003-task-node-state-spec.md)（§3.2 状态机修订）
- [api-standards.md](../standards/api-standards.md)（§4.2 事件表）
- [environment-config-standards.md](../standards/environment-config-standards.md)
