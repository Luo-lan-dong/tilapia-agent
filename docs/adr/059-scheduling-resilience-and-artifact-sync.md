# ADR-059: 调度准入与产物韧性（可用内存/负载双门槛 + 10s 排队重扫 + 增量产物同步 + 故障改派）

## 状态

Accepted

## 日期

2026-09-28

## 问题背景

一次真实派发失败暴露了中枢调度层与产物链路的四个缺陷。现场证据：

- 节点日志：`task_dispatch_rejected_resource_busy`（节点自评拒单），任务回 `pending`，
  前端**只显示「排队中 0%」且没有任何原因**；
- 中枢 AI 自检：`task_startup_stalled:60` 但无法解释为何失败，并把根因误判为「节点运行环境问题」；
- 库内实测：同一被拒任务 23 分钟后仍 `pending`；`笔记本` 节点内存 82.7% 被判「合规」，
  而 `code` 节点 43% 被判「更优」；
- 用户反馈：任务失败后**无法改派**，只能新建任务链 → 三件套被重新生成；
  AI 传入的节点偏好「被记录但未改变最终选择」。

### 缺陷清单

| 编号 | 缺陷 | 代码依据 |
|------|------|---------|
| S1 | **准入判据用相对百分比**：`memory.percent` 是各机器自己的分母。4G 机器 82.7%（仅剩 ~0.7G）判「合规」，8 核机器 43%（剩 2.3G）反而与前者不可比；小内存机器被系统性低估风险 | `dispatch_decision_service.py:89-94`、`config.py:122-133`（默认 85%）、节点侧 `hub_client._resource_busy`（瞬时采样 ≥90%） |
| S2 | **拒单原因不可见**：节点上报的 `reason` 只进日志（`task_reject_reason`），不落任务、不进对话 | `task_event_handler.py:231-247` |
| S3 | **AI 只能在「预筛后的残集」里选**：候选集先被阈值/回避过滤，AI 拿到的 `task_candidates` 已排除不达标节点，偏好目标若被筛掉则**根本无从选择** | `dispatch_decision_service.py:66-106`、`:180-189`（白名单强校验） |
| S4 | **执行中产物不同步，离线即丢失**：只有「清单准实时（≥30s 节流）+ 内容按需拉取 + 完成时全量 zip」。任务执行途中节点离线 → 全量 zip 永不生成 → 产物无法跨节点取用，改派只能从头再来 | `task_event_handler.py:56-80`（`_schedule_artifact_sync` 仅在 completed 调用）、`artifact_service.py:171-245` |

> 注：ADR-028 当年明确否决了「文件变更事件推送（`agent.file_changed`）」并留下
> 「留作后续演进」——本决策即是那次演进的**受限实现**（增量拉取而非推送）。

## 决策

与用户确认（2026-09-28）：

### D1 · 准入判据：绝对可用内存 + 综合负载（双门槛）

单台节点的「可自主派发」条件：

```
available_memory_gb > 1.0        # metrics.memory.available_gb（绝对值，非百分比）
AND load_score < 80              # metrics.load_score.score（综合负载，已平滑）
```

- **两者任一不满足即视为「不可自主派发」**（用户决策：更保守）。此时不再由 AI 静默决定，
  必须**向用户发起询问**（见 D2）；
- 判据来源统一为心跳 `metrics_snapshot`（已有字段，无需新增采集）；
- 中枢侧 `dispatch_cpu_threshold_percent` / `dispatch_mem_threshold_percent` 退役为
  **兜底安全阈值**（默认放宽，仅在 metrics 缺失时降级使用），主判据改为上述双门槛；
- 节点侧 `_resource_busy` 同步改用同一口径（可用内存 + `load_score`），
  与中枢一致，消除「中枢放行、节点拒单」的双门槛错配。

### D2 · 派发决策：规则先行、AI 仅在「可自主」时决策，否则问用户

10 秒调度轮（复用现有 `SCHEDULER_INTERVAL_SECONDS = 10`，已满足用户要求）内：

**场景一：用户/AI 未指定节点**

```
候选 = 在线 + 有并发槽位 + 未被回避
├─ 候选中有节点满足「可自主」（D1）
│    ├─ 仅 1 个 → 规则直达（跳过 LLM，零 token）
│    └─ ≥ 2 个 → AI 自主决策（在满足门槛的候选内选优）
└─ 无节点满足「可自主」
     └─ 不静默等待、不硬塞：**向用户发起询问**（对话中给出候选与各自缺口）
```

**场景二：用户/AI 显式指定节点 A**

```
任务进入排队态（记录 target_node = A）
每 10s 扫描 A：
├─ A 满足「可自主」→ 无需 AI，直接派送
└─ A 不满足 → 继续排队；向用户提示「A 当前不足：可用内存 x GB / 负载 y」
     └─ 用户可在对话中改派（AI 据此更换 target_node）
```

**唯一例外**：用户**明确要求强行派送**时，允许跳过 D1 门槛（记录
`forced_dispatch=true` 与操作者，审计留痕）。除此之外任何情况下都不得硬塞给不达标节点。

### D3 · 拒单与排队：原因可见、状态可见

- `agent.task_rejected` 的 `reason` 落库到任务（新增 `last_reject_reason`），
  前端「排队中」展示「节点 X 因 <原因> 拒单，已回避 5 分钟」；
- 新增排队态提示：任务在 `pending` 且无可自主节点时，向对话推送一条系统消息
  （含候选节点与各自缺口数值），而不是静默排队；
- 节点侧拒单日志补齐真实读数（CPU/内存/负载/阈值）——本次事故就是因为日志没记读数，
  排查只能靠事后查库推断。

### D4 · 产物韧性：执行中增量同步（S2）

**执行中每 30 秒一轮增量同步**（复用现有 `agent.file_tree` 的 `size` + `mtime`）：

```
中枢对每个在跑任务：
  拉取该任务最新 file_tree（已有周期上报）
  与上一次快照做差集 → 变更/新增文件列表
  逐个 hub.file_fetch(mode=file) 拉取 → 落 {artifact_cache_dir}/{task_id}/
  更新快照
任务完成 → 仍执行现有全量 zip（export.zip），作为链上现场基准
```

- 离线丢失窗口从「整段开发」收敛到 **≤ 30 秒**；
- **不新增协议事件**（复用 `file_tree` + `file_fetch` + `file_chunk`），
  规避 ADR-028 当年否决「文件变更事件推送」时的复杂度顾虑；
- S3（写即推送的准实时 `agent.file_changed`）**登记为后续演进**，本次不做。

### D5 · 故障改派：先判后告 / 先问后派

触发条件：任务 `failed`，或执行中节点心跳超时被判 `offline`。

```
if AI 智能调度已开启（system_settings.ai_dispatch_enabled）:
    直接改派（用最近一次已同步产物 + 链上下文作为现场）
    然后向用户告知改派事实与目标节点
else:
    先向用户询问是否改派、改派到哪台；确认后执行
```

- 改派**复用**既有文档与产物：`handoff` 现场包（`build_handoff_payload`）+
  链上下文 `project_context.md`，**不重新生成三件套**；
- 用户要求「改派另一个节点」时，AI 通过对话即可更换 `target_node`（走 D2 场景二）。

## 备选方案

| 方案 | 结论 |
|------|------|
| **A（本决策）** 双门槛 + 排队重扫 + 增量同步 + 先判后告 | **采纳**（用户逐条确认） |
| B. 继续用内存百分比，只把阈值调低 | 否决：百分比的分母差异是结构性问题，调参无法修复小内存机器的低估 |
| C. 无节点可自主时静默排队等下一轮 | 否决（用户明确要求）：用户看不到「为什么在等」，本次事故正是这种静默 |
| D. 指定节点时绕过门槛直达 | 否决（用户明确要求）：会把任务塞给可用内存 < 1G 的机器；仅保留「用户显式强行派送」这一例外 |
| E. 产物 S3：写即推送（`agent.file_changed`） | 本次不做，**登记为后续演进**（协议/流量复杂度最高，ADR-028 已否决） |
| F. 改派时重新生成三件套 | 否决：与 ADR-012/ADR-036 迭代链决策冲突，且浪费 token |
| G. 故障一律自动改派 | 否决（用户决策）：未开 AI 智能调度时需人工确认，避免把任务悄悄挪到不合适的节点 |

## 影响

### 中枢（`opc-central-dispatch-hub`）

| 文件 | 计划变更 |
|------|---------|
| `app/core/config.py` | 新增 `dispatch_min_available_memory_gb`（默认 1.0）、`dispatch_max_load_score`（默认 80.0）；既有 CPU/内存百分比阈值降级为兜底 |
| `app/services/dispatch_decision_service.py` | `list_available_nodes` 改双门槛判定（metrics 绝对可用内存 + load_score）；新增 `classify_eligibility` 产出「可自主/不可自主」与缺口描述；候选**不再按门槛直接剔除**，而是标注可否自主（保留 AI/用户指定节点的可见性） |
| `app/services/scheduler.py` | 场景一/场景二分支：指定节点排队重扫；无自主候选时推送询问（而非静默）；forced_dispatch 审计 |
| `app/models/task.py` | 新增 `target_node_id`（指定节点）、`last_reject_reason`（拒单原因）、`dispatch_hold_reason`（排队原因）；`forced_dispatch` 审计字段 |
| `app/services/task_event_handler.py` | 拒单原因落库；新增执行中增量同步调度（D4） |
| `app/services/artifact_service.py` | 新增增量同步：按 `file_tree` 差集拉取变更文件（复用 `fetch_file`） |
| `app/services/node_heartbeat.py` | 离线重置任务后按 D5 触发「改派或询问」 |
| `alembic/versions/*` | 上述新列迁移（含 downgrade） |

### 节点（`opc-agent-programming`）

| 文件 | 计划变更 |
|------|---------|
| `app/client/hub_client.py` | `_resource_busy` 改「可用内存 > 1G 且 load_score < 80」；拒单日志带真实读数与阈值 |
| `app/utils/monitor.py` | 暴露 `available_gb`（已有）供自评使用；保持 `load_score` 计算不变 |

### 前端（`opc-web-console`）

- 任务摘要面板：排队态显示「等待节点」与缺口原因（可用内存/负载数值）；
- 任务详情：显示 `last_reject_reason`；`failed`/离线任务显示「改派」入口。

### 兼容性

- 新旧节点共存：旧节点不认 `target_node_id` 等新字段，按现状（中枢自选节点）运行；
- 增量同步对旧节点无影响（复用既有 `file_tree`/`file_fetch`）；
- metrics 缺失（旧节点未上报）时回退既有百分比兜底阈值，行为与现状一致。

## 实施状态（2026-09-28）

**已落地**：

| 决策 | 落地方式 | 验证 |
|------|---------|------|
| D1 双门槛（中枢） | `dispatch_decision_service.is_node_eligible` / `eligibility_gap` / `list_eligible_nodes` / `diagnose_eligibility`；阈值 `DISPATCH_MIN_AVAILABLE_MEMORY_GB`(1.0) / `DISPATCH_MAX_LOAD_SCORE`(80.0) | 9 个单测（含边界 1.0/80.0、4G 82.7% vs 8 核 43%、旧节点兼容） |
| D1 双门槛（节点） | `monitor.get_available_memory_gb` / `get_load_score` + `hub_client._resource_busy` 同口径（保留两次采样防抖） | 10 个单测；节点全量 182 passed |
| D2 场景一 | 调度器四路分类：`list_eligible_nodes` 为空 → 落 holds（不静默、不硬塞）；命中 1 台规则直达；≥2 台且 AI 开启交 AI | `_classify_pending_tasks` + 既有 AI 决策测试 |
| D2 场景二 | `tasks.target_node_id` 排队 + 每 10s `_resolve_target_task` 复检直达（跳过 AI）；`PUT /{task_id}/dispatch-preference` 与 `smart_dispatch` 技能可指定 | 指定节点路径 + `dispatch_task(force=...)` |
| D2 例外 | `forced_dispatch` 列 + 仅 `force=true` 可越门槛 + `task_forced_dispatched` 审计日志 | 是 |
| D3 可见 | `last_reject_reason` / `dispatch_hold_reason` 落库；`GET /{task_id}/eligibility` 给出候选缺口与建议；前端 `TaskTable`「排队中 / 等待 X」+ tooltip、`TaskDetailInfo` 三项 | 前端 4 例 + `tsc` 通过 |
| D4 增量同步 | `ArtifactService.select_changed_files` / `sync_incremental`（size+mtime 差集，单轮 ≤20 文件 / 单文件 ≤2MB）；由在途任务的 `agent.file_tree` 驱动（同任务单飞） | 11 个单测（含失败隔离、状态损坏容错） |

**与原文的偏差（实际实现为准）**：

- `classify_eligibility` → 落地为 `is_node_eligible` + `eligibility_gap`（布尔 + 缺口文本，覆盖调度/诊断/前端三处）；
- `list_available_nodes` **保持原语义**（百分比预筛作旧节点兜底），其上新增 `list_eligible_nodes` 作为 AI 候选域——而非把预筛整体改成「标注不剔除」；
- D4 由**节点侧 30s 上报事件驱动**，未在中枢另设定时器（节拍等价，省去任务态扫描）；
- D3「向对话推送系统消息」落地为**落库 + 查询接口 + 结构化日志**：中枢没有任务级事件总线，新增总线的复杂度超出本次范围，落库对前端反而更可靠。

**未完成（不得据此认为 D5 已全部实现）**：

1. ~~D5「AI 关闭时先询问」分支未实现~~ → **已实现（2026-09-28 第二批）**：
   新增 `requeue_pending`（待确认改派）挂起态，调度器**显式排除**该状态；
   `reset_tasks_for_offline_nodes` 按 `ai_dispatch_enabled` 分流（开启 → `pending` 自动改派；
   关闭 → `requeue_pending` 等确认）；`POST /tasks/{id}/confirm-redispatch` 为确认入口；
   前端任务详情与列表展示「待确认改派」及确认按钮。落地明细见
   [059-scheduling-resilience-d5-ask-branch.md](../spec/059-scheduling-resilience-d5-ask-branch.md)。
   **仍存差距**：「对话系统消息」以 `dispatch_hold_reason` 落库 + 前端展示 + 会话内可读实现，
   未新增中枢→会话的主动推送通道。
2. **「改派不重生成三件套」缺端到端回归用例**——机制上依赖既有 handoff（ADR-034）与
   本次增量产物；**单元层已锁定**（`test_failed_task_can_be_redispatched_without_regenerating_docs`
   断言 `specification` 逐字不变），但缺真实库 IT/E2E 验证；
3. 前端**已补**「指定节点 / 强行派送 / 确认改派」控件（2026-09-28 第二批）。

### 改派能力（已落地，2026-09-28 补充）

`TaskService.set_dispatch_preference` 支持 **failed 任务改派**：

```
failed 任务 + 指定节点（或 force）
  → 回 pending、恢复 dispatch_type=auto 与派发窗口、progress 归零
  → 复用既有 specification / handoff 现场 / 已同步产物（**不重新生成三件套**）
  → 用户显式指定即清除该任务的节点回避记录，指定节点可立即被重新考虑
```

边界：`completed` 拒绝改派（应走任务链迭代）；`running`/`assigned` 拒绝改派
（需先取消或等终态），避免与在跑节点状态冲突。

## 关联文档

- [059-scheduling-resilience-spec.md](../spec/059-scheduling-resilience-spec.md)
- [059-scheduling-resilience-harness.md](../spec/059-scheduling-resilience-harness.md)
- [059-scheduling-resilience-changes.md](../spec/059-scheduling-resilience-changes.md)
- [ADR-032](./032-concurrent-task-scheduling.md)（本决策修订其预筛判据与拒单语义）
- [ADR-028](./028-task-workspace-binding-and-artifact-sync.md)（本决策实现其「留作后续演进」的准实时产物同步）
- [ADR-012](./012-tilacode-execution-layer-integration.md) / [ADR-036](./036-session-task-chain-and-spec-command.md)（改派复用文档与现场，不重生成）
- [ADR-034](./034-task-handoff-context.md)（handoff 现场包与链上下文，改派的数据来源）
- [ADR-017](./017-task-dispatch-type-and-window.md)（manual/auto 与派发窗口，场景二排队与之协同）
