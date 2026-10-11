# ADR-032: 节点并发任务调度（资源水位预筛 + Agent 自评拒绝 + 改派等待）

## 状态

Accepted（预筛判据与拒单/排队语义已被 [ADR-059](./059-scheduling-resilience-and-artifact-sync.md) 修订，见该 ADR D1/D2/D3）

## 日期

2026-09-12

## 问题背景

当前调度为「单任务互斥」设计（见 task-node-state-spec §3.2 与 ADR-017）：

1. **中枢**：\[scheduler.py] 只匹配 `status == idle` 的节点，分配后立即置 `busy`，
   其他任务对该节点排队等待。
2. **Agent**：`hub_client._handle_task_dispatch` 为单执行槽
   （`_execution_task`），已有任务执行中时回复 `agent.task_rejected`；
   `task_bridge` 的 `_current_task` / `_consumer` 亦为单数设计。

编程任务的特征是**绝大部分时间阻塞在 LLM 响应上**，节点 CPU/内存大量空闲。
单任务互斥导致节点资源浪费、任务排队延迟，用户明确要求：

> 不应该以「只能执行一个任务」作为排队标准，应从节点系统资源占用判断
> 该节点是否还能执行下个任务。

有利条件：ADR-014 已上线节点真实资源监控（psutil 采集 CPU/内存/磁盘，
心跳携带 metrics 快照与综合负载分落库），调度所需数据现成可用。

## 决策

与用户确认（2026-09-12，方案 B 变体）：

1. **中枢资源预筛**：调度候选节点 = 在线（心跳新鲜）且心跳 metrics 中
   CPU/内存使用率低于阈值（`DISPATCH_CPU_THRESHOLD_PERCENT` /
   `DISPATCH_MEM_THRESHOLD_PERCENT`，默认 85）且**在跑任务数**小于节点
   `max_concurrent_tasks`。在跑任务数由 tasks 表实时统计
   （`status IN (assigned, running) AND assigned_node_id = node`），
   不再依赖节点 `busy` 状态。排序仍按综合负载分 `load` 升序。
2. **节点并发槽位**：`agent_nodes` 新增 `max_concurrent_tasks` 列
   （`INTEGER NOT NULL DEFAULT 2`），Alembic 迁移；MVP 不提供页面配置，
   由迁移默认值统一生效。
3. **Agent 自评拒绝**：Agent 收到派单时本地校验——在跑任务数 ≥
   `AGENT_MAX_CONCURRENT_TASKS`（默认 2）或 psutil 实时 CPU/内存 ≥
   `AGENT_BUSY_CPU_THRESHOLD_PERCENT` / `AGENT_BUSY_MEM_THRESHOLD_PERCENT`
   （默认 90，比中枢阈值更严格，作为瞬时尖峰兜底）→ 回复
   `agent.task_rejected`，payload 新增 `reason` 枚举：
   `slots_full`（槽位满）/ `resource_busy`（资源水位高）/
   `bridge_not_ready` / `shutting_down`。缺省按 `slots_full` 兼容旧 Agent。
4. **拒绝改派**：中枢收到 `agent.task_rejected` → 任务回 `pending`，
   进程内记录「任务 → 已拒绝节点集合」（随任务改派成功或 5 分钟 TTL 清除），
   下一轮调度回避已拒绝节点，改派其他候选节点；无其他候选时保持 pending。
5. **等待再分配**：所有节点均不可用（离线/超阈值/槽位满）时任务保持
   `pending`，调度循环（现有 10s 周期）持续扫描，节点释放资源或任务
   完成后自然派发——无需新增等待队列机制。
6. **状态机语义调整**：节点 `busy` 降级为**纯展示态**（在跑任务数 > 0
   即 busy，心跳上报不再覆盖/恢复 busy 的调度语义），不再是调度排除条件；
   `offline` / `error` 仍排除。task-node-state-spec §3.2 同步修订。

明确不做：

- **不做** 任务级节点亲和/粘性调度（同一任务链仍由 base\_task\_id 根任务
  目录语义保证，迭代任务由用户手动指定或首轮调度决定）。

- **不做** Agent 侧任务内部排队：槽位满即拒绝，由中枢统一改派/等待，
  避免节点本地排队导致的中枢不可见状态。

- **不做** 节点 max\_concurrent\_tasks 的页面配置入口（MVP 用默认值，
  后续按需开放）。

## 备选方案

| 方案                                 | 结论                                                  |
| ---------------------------------- | --------------------------------------------------- |
| A. 中枢资源预筛 + Agent 自评拒绝 + 改派等待（本决策） | 采纳。预筛利用现成心跳数据，自评兜底心跳延迟盲区，改派/等待复用现有调度循环，无新基础设施       |
| B. 纯中枢槽位判定（无 Agent 自评）             | 否决。心跳 30s 级延迟，节点瞬时资源尖峰（如本地编译）无法及时反映，可能把任务派给实际已繁忙的节点 |
| C. 仅资源阈值、不设槽位上限                    | 否决。任务量突增时单节点可能积压过多并发任务，触发 LLM 限速/内存耗尽，故障面更大         |
| D. Agent 本地排队不拒绝                   | 否决。排队状态中枢不可见，与「中枢统一管理任务状态」原则冲突                      |

## 影响

### 对中枢的影响

- `scheduler._find_best_idle_node` 重写为 `_find_best_available_node`：
  在线 + 资源水位 + 在跑数 < 槽位上限；行锁保留（防并发竞争）。

- `_assign_task_to_node`：不再置节点 `busy`（由在跑数派生展示态），
  `load` 仍由心跳综合分维护，不再随分配 +1。

- `task_event_handler`：`agent.task_rejected` 处理扩展——回 pending +
  进程内回避记录；任务释放逻辑（completed/failed/rejected）不再
  「恢复节点 idle」而仅更新任务态。

- `agent_nodes` 表新增 `max_concurrent_tasks` 列 + Alembic 迁移。

- `config.py` 新增 `DISPATCH_CPU_THRESHOLD_PERCENT` /
  `DISPATCH_MEM_THRESHOLD_PERCENT`（Pydantic Settings，.env.example 同步）。

- 节点心跳处理：`busy` 不再由任务生命周期强制覆盖（保留心跳上报值）。

### 对 Agent 的影响

- `hub_client._handle_task_dispatch`：单执行槽改为多槽
  （`Dict[task_id, asyncio.Task]`），拒绝时携带 `reason`。

- `task_bridge` 并发化：`_current_task` / `_consumer` 改为按 task\_id 的
  字典；`_handle_todo_updated` 按 sessionID 路由到对应 consumer；
  `interrupt` / `handle_tilacode_crash` 按 task\_id 定位
  （tilacode 崩溃时**所有**在跑任务均失败）。

- `config.py` 新增 `AGENT_MAX_CONCURRENT_TASKS` /
  `AGENT_BUSY_CPU_THRESHOLD_PERCENT` / `AGENT_BUSY_MEM_THRESHOLD_PERCENT`。

- 心跳 payload 增报 `running_task_count`（展示与审计用）。

### 对协议的影响

- `agent.task_rejected` payload 新增可选 `reason` 枚举字段（向后兼容：
  缺省按 `slots_full` 处理）。api-standards §4.2 同步登记。

- `agent.heartbeat` payload 新增可选 `running_task_count`。

- 无新增事件类型。

### 对前端的影响

- 节点列表/详情可在后续迭代展示「在跑 x/y」与资源水位；本期不强制改动
  （busy 展示态语义不变，UI 无破坏）。

### 兼容性

- **旧 Agent + 新中枢**：旧 Agent busy 时拒绝（无 reason）→ 中枢按
  `slots_full` 改派其他节点/等待，行为不差于现状。

- **新 Agent + 旧中枢**：不涉及（中枢与 Agent 同版本发布节奏，
  且中枢先行部署）。

- **LLM 并发限速风险**：多任务并发共享同一 Provider Key，可能触发 RPM
  限制导致单任务失败——由既有任务失败/重试机制承接，可接受。

## 修订记录

- **2026-09-28（ADR-059 D1/D2/D3）**：本 ADR 的**预筛判据**与**拒单/排队语义**被修订：
  1. **预筛判据**：原按心跳 `metrics` 的 **cpu/memory 百分比**（默认 85%）剔除候选 → 改为**双门槛绝对值判定**：
     `metrics.memory.available_gb > 1.0` **且** `metrics.load_score.score < 80`，**任一不满足即「不可自主」**。
     原因：百分比的分母随机器规格变化——4G 机器 82.7% 仅剩 ~0.7G 却被判「合规」，小内存机器被系统性低估风险。
     既有百分比阈值降级为 **metrics 缺失时的兜底**（旧节点兼容）。
  2. **候选集语义**：门槛结果**不再直接剔除候选**（原实现使 AI 只能在残集里选，偏好目标被筛掉即无从选择），
     改为**标注可否自主**：不可自主节点仍可见、可被用户/AI **指定排队**。
  3. **拒单语义**：`agent.task_rejected` 的 `reason` 由「仅记日志」改为**落库 `last_reject_reason` 并对用户可见**；
     无可自主节点时**不再静默排队**，而是向用户发起询问（含各候选缺口数值）。
  4. **AI 决策边界**：AI 仅在「存在可自主候选」时自主决策；否则必须询问用户。
     **唯一例外**：用户明确要求强行派送（审计 `forced_dispatch=true`）。
  5. **指定节点**：新增 `target_node_id`，进入排队并由 10s 调度轮重扫，达标当轮**直达且不经 AI**；
     用户可通过对话改派。
- 并发槽位上限、资源水位预筛与「Agent 自评拒绝」的**总体架构不变**，本次仅修订判据口径与可见性。

## 关联文档

- [ADR-014 Agent 节点真实系统资源监控](./014-agent-node-metrics.md)（metrics 数据来源）

- [ADR-017 任务派发模式](./017-task-dispatch-type-and-window.md)（调度窗口复用）

- [003-task-node-state-spec.md](../spec/003-task-node-state-spec.md)（§3.2 状态机修订）

- [api-standards.md](../standards/api-standards.md)（§4.2 task\_rejected 扩展）

- [032-concurrent-task-scheduling-spec.md](../spec/032-concurrent-task-scheduling-spec.md)

- [032-concurrent-task-scheduling-harness.md](../spec/032-concurrent-task-scheduling-harness.md)

- [032-concurrent-task-scheduling-changes.md](../spec/032-concurrent-task-scheduling-changes.md)

- [ADR-059 调度准入与产物韧性](./059-scheduling-resilience-and-artifact-sync.md)（本 ADR 判据与拒单语义的修订来源）

