# 调度准入与产物韧性 Spec（双门槛 + 排队重扫 + 增量同步 + 故障改派）

> 关联：[ADR-059](../adr/059-scheduling-resilience-and-artifact-sync.md) · [Harness](./059-scheduling-resilience-harness.md) · [Changes](./059-scheduling-resilience-changes.md)
> 上游：[ADR-032](../adr/032-concurrent-task-scheduling.md)（预筛与拒单语义）· [ADR-028](../adr/028-task-workspace-binding-and-artifact-sync.md)（产物回传）· [ADR-034](../adr/034-task-handoff-context.md)（现场迁移）

## 1. 功能描述

修复中枢调度层与产物链路的四个缺陷（S1~S4），并落地用户确认的新调度语义：

1. **准入判据改为「绝对可用内存 + 综合负载」双门槛**（不再用各机器自定义分母的百分比）；
2. **AI 只在「可自主」时决策，否则询问用户**；无法自主时不静默排队、不硬塞；
3. **拒单原因与排队状态对用户可见**；
4. **执行中增量同步产物**（离线丢失窗口 ≤ 30 秒），故障改派**复用**已有文档与产物；
5. 后续演进（**本次仅登记不做**）：S3 写即推送的准实时产物同步（`agent.file_changed`）。

## 2. 用户故事

- **US-1（判据公平）**：作为运维，4G 机器不该因为「82.7%」被判合规、8 核机器 43% 被判另一档——
  准入应按**还剩多少内存**与**综合负载**判断，与机器规格无关。
- **US-2（不自作主张）**：作为用户，当没有节点处于健康水位时，AI **必须问我**，而不是替我决定；
  只有我明确要求「强行派送」时才允许越过门槛。
- **US-3（看得见）**：作为用户，任务排队时我能看到「在等哪台节点、缺什么（可用内存/负载数值）」，
  以及节点拒单的真实原因，而不是只有一句「排队中 0%」。
- **US-4（指定即承诺）**：作为用户/AI，我指定节点 A 后，任务应**排队等 A**，A 一达标立即派送（不必再经 AI）；
  若我想换，跟 AI 说一声即可改派。
- **US-5（不重做）**：作为用户，任务失败或节点离线时改派，AI 应拿已有的文档与产物继续，
  **不重新生成三件套**。
- **US-6（丢了也少）**：作为用户，节点开发途中突然离线，最多损失最近 30 秒的改动，
  而不是整段开发的成果。

## 3. 核心流程

### 3.1 准入判定（D1）

```
节点可自主派发 ⇔
    metrics.memory.available_gb > 1.0
    AND metrics.load_score.score < 80

任一不满足 → 该节点「不可自主」（可被指定排队，但不得自动派发）
metrics 缺失（旧节点） → 回退既有百分比兜底阈值（DISPATCH_CPU/MEM_THRESHOLD_PERCENT）
```

节点侧本地自评（`_resource_busy`）采用**同一口径**，消除「中枢放行、节点拒单」错配。

### 3.2 派发决策（D2）

```
每 10 秒一轮（SCHEDULER_INTERVAL_SECONDS）

任务有 target_node（用户/AI 指定）：
    if target 在线 and 有槽位 and 可自主  → 直接派送（跳过 AI）
    elif 用户明确要求强行派送            → 派送（审计 forced_dispatch=true）
    else                                → 保持排队 + 推送缺口提示（不静默）

任务无 target_node：
    candidates = 在线 + 有槽位 + 未回避
    eligible   = candidates ∩ 可自主
    if eligible 非空:
        len == 1 → 规则直达（跳过 LLM）
        len >= 2 → AI 在 eligible 内自主决策
    else:
        → 不派发；向用户发起询问（列出 candidates 与各自缺口）
```

### 3.3 拒单与排队可见（D3）

```
节点拒单(agent.task_rejected, reason=resource_busy/...)
  → 任务回 pending + 记录 last_reject_reason + 记入回避集合(5min TTL)
  → 对话/任务摘要展示：「节点 X 因 <原因> 拒单，已回避 5 分钟」
无可自主节点时 → 推送排队提示（候选与缺口数值），而非静默等待
```

### 3.4 执行中增量产物同步（D4）

```
每 30 秒（在跑任务）：
  拿到最新 file_tree（节点已周期上报）
  diff = 相对上次快照新增/变更的文件（size 或 mtime 变化）
  for f in diff: hub.file_fetch(mode=file, path=f) → 落 {artifact_cache_dir}/{task_id}/
  更新快照
任务 completed → 仍走全量 zip（export.zip），作为链上现场基准
节点 offline  → 已同步部分即改派现场
```

### 3.5 故障改派（D5）

```
触发：任务 failed，或执行中节点被判 offline
if ai_dispatch_enabled:
    自动改派（现场 = 已同步产物 + project_context.md + handoff 包）→ 告知用户
else:
    询问用户是否改派/改派到哪台 → 确认后执行
改派一律复用文档与产物，不重新生成三件套
```

## 4. 降级链路

| 异常场景 | 行为 |
|---------|------|
| 节点 metrics 缺失（旧节点） | 回退既有百分比兜底阈值；不因缺字段而拒派 |
| `load_score` 缺失但 CPU 可读 | 用 CPU 百分比兜底判定，日志标注口径降级 |
| 增量同步拉取失败（单文件） | 记 WARNING 跳过该文件，下轮重试；不阻断任务 |
| 增量同步整体失败（节点离线） | 以已同步部分为现场；改派后由新节点从该现场继续 |
| 产物缓存目录写满 | 记 ERROR + 停止本轮增量同步；不阻断任务执行 |
| 指定节点长期不达标 | 排队保持 + 周期性提示；询问用户是否改派（不自动放弃） |
| 用户指定节点已离线 | 立即提示「目标节点离线」，等待用户决定改派或继续等 |
| AI 智能调度开启但 LLM 调用失败 | 回退规则：在可自主候选中选负载最低者；记 `decision_source=rule` |
| 强行派送被拒（节点仍拒单） | 记录拒单原因；不再自动重试同一节点，转为询问用户 |
| 改派时现场包过大（超 `handoff_max_bundle_bytes`） | 提示用户；提供「仅代码/仅文档」或跳过现场的选项 |

## 5. 数据结构

### 5.1 `tasks` 新增列

```python
target_node_id: Mapped[uuid.UUID | None]   # 用户/AI 指定的目标节点（排队等待对象）
last_reject_reason: Mapped[str | None]     # 最近一次节点拒单原因（String(64)）
dispatch_hold_reason: Mapped[str | None]   # 当前排队原因（String(200)，含缺口数值）
forced_dispatch: Mapped[bool]              # 是否用户明确要求越过门槛（默认 false）
```

### 5.2 排队提示（对话系统消息）

```jsonc
{
  "kind": "dispatch_hold",
  "task_id": "...",
  "reason": "无可自主派发节点",
  "candidates": [
    {"name": "code", "available_memory_gb": 0.8, "load_score": 62.1, "blocking": "memory"},
    {"name": "笔记本", "available_memory_gb": 2.4, "load_score": 83.5, "blocking": "load"}
  ],
  "hint": "可指定节点排队等待，或要求强行派送"
}
```

## 6. 验收标准

- [ ] **D1 双门槛**：可用内存 0.9G / 负载 50 的节点判「不可自主」；可用内存 2G / 负载 85 判「不可自主」；两者都达标才可自主（任一不满足即不可自主）；
- [ ] **D1 口径一致**：中枢与节点本地自评对同一节点给出相同结论（不再出现中枢放行、节点拒单）；
- [ ] **D1 绝对内存**：4G 机器 82.7%（剩 ~0.7G）判不可自主；8 核机器 43%（剩 2.3G）判可自主；
- [ ] **D2 场景一**：无 eligible 节点时**不派发**且推送询问；有 1 个 eligible 时规则直达（不调 LLM）；≥2 个时由 AI 在 eligible 内选择；
- [ ] **D2 场景二**：指定节点 A 后进入排队，A 达标当轮（≤10s）派送且不经 AI；A 不达标时持续排队并提示缺口；
- [ ] **D2 例外**：用户明确要求强行派送时才允许越过门槛，且审计记 `forced_dispatch=true`；
- [ ] **D3 可见**：任务排队时前端显示候选与缺口数值；拒单后显示 `last_reject_reason`；
- [ ] **D4 增量同步**：执行中修改文件，30 秒内中枢缓存出现该文件最新内容；离线后该内容可被改派使用；
- [ ] **D5 先判后告**：AI 调度开启时，节点离线 → 自动改派 + 告知用户（无需先询问）；
- [ ] **D5 先问后派**：AI 调度关闭时，节点离线 → 先询问用户，确认后才派；
- [ ] **D5 不重生成**：改派任务的 `specs/` 目录不产生新版本文档（复用原文档与现场）；
- [ ] **兼容**：旧节点（无 metrics/新字段）仍可被派发，行为不回归；
- [ ] **登记**：S3（写即推送）在 Spec 与本 ADR 中登记为后续演进，本次 `agent.file_changed` 不实现。

## 7. 关联文档

- [ADR-059](../adr/059-scheduling-resilience-and-artifact-sync.md)
- [059-scheduling-resilience-harness.md](./059-scheduling-resilience-harness.md)
- [059-scheduling-resilience-changes.md](./059-scheduling-resilience-changes.md)
