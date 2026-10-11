# ADR-059 D5「先询问」分支实现明细（待确认改派挂起态）

> 关联：[ADR-059](../adr/059-scheduling-resilience-and-artifact-sync.md)（D5 决策）· [Spec](./059-scheduling-resilience-spec.md) · [Harness](./059-scheduling-resilience-harness.md) · [Changes](./059-scheduling-resilience-changes.md)
> 性质：ADR-059 D5「AI 关闭时先询问」分支的**落地设计**。ADR-059 只定了行为（「先向用户询问是否改派、
> 改派到哪台；确认后执行」），未定挂起态与交互载体；本文补齐该缺口。用户已确认交互载体为
> **对话系统消息 + 用户回复确认**（2026-09-28）。

## 1. 问题

ADR-059 D5 要求：

```
if AI 智能调度已开启: 直接改派（复用现场）→ 告知用户
else:                先询问是否改派/改派到哪台 → 确认后执行
```

当前实现只有前半句：节点离线/任务失败一律把任务置 `pending`，**下一轮调度器自动派发**，
不区分 AI 开关——等于「未开 AI 时也悄悄改派了」，与用户明确否决的备选方案 G 一致。

要落地「先询问」，缺一个**挂起态**：任务必须停在「已重新排队但**不允许**自动派发」上，
直到用户确认。

## 2. 决策

### 2.1 新增任务状态 `requeue_pending`（待确认改派）

| 状态 | 语义 | 调度器是否派发 |
|------|------|--------------|
| `pending` | 正常排队，等待派发 | ✅ |
| **`requeue_pending`** | **因故障（离线/失败）重新排队，等待用户确认改派** | ❌ 显式排除 |
| `review` | plan 方案待人工批准 | ❌ |

- `requeue_pending` 是**非终态**：用户确认后回 `pending`（交调度器），也可改派/取消；
- `dispatch_hold_reason` 承载询问文案（复用 ADR-059 D3 已有列，前端已展示）；
- **不新增列、不新增表**，只扩一个状态值 + 两处 CHECK 约束迁移。

### 2.2 触发与分流（`node_heartbeat.reset_tasks_for_offline_nodes`）

```
节点被判 offline，其 assigned/running 任务重新排队：
if ai_dispatch_enabled:                      # AI 开启
    status = pending                         # 直接改派（现状保持）
    hold_reason = "执行节点离线，任务已自动改派；已同步的产物将作为现场"
else:                                        # AI 关闭 → 先询问
    status = requeue_pending
    hold_reason = "执行节点「X」离线，任务已停止。是否改派？（确认后交调度器重新派发）"
```

### 2.3 用户确认入口

| 端点 | 语义 |
|------|------|
| `POST /tasks/{task_id}/confirm-redispatch` | 确认改派：`requeue_pending` → `pending`，清空 `dispatch_hold_reason`，清除回避记录 |

- 非 `requeue_pending` 状态调用返回 409（幂等保护：重复点击不会把执行中任务拉回队列）；
- 用户也可用已有的 `PUT /dispatch-preference` 指定节点改派（该端点接受 `requeue_pending`）；
- 前端在排队原因旁展示「确认改派」按钮（仅 `requeue_pending` 可见）。

### 2.4 「对话系统消息」的落地方式

用户选择「对话系统消息询问」，但**中枢没有任务级对话通道**（同一结论已在 ADR-059 D3 得出）。
本设计按 D3 的既定折中处理，并保持语义等价：

- 询问以**结构化日志 + `dispatch_hold_reason` 落库**发出，前端任务列表/详情与
  `GET /tasks/{id}/eligibility` 均可读；用户回复即点击「确认改派」按钮；
- 若任务关联了构建会话（`session_id` 非空），中枢 AI 在后续对话中可读到该任务的
  `requeue_pending` + `dispatch_hold_reason`，从而在对话里主动询问——这是「系统消息」的等价实现，
  不新增对话基础设施。

## 3. 兼容性

| 场景 | 行为 |
|------|------|
| 存量任务 | 新状态只新增枚举值，存量行不受影响 |
| 旧前端 | 未知状态按「其他」展示；`dispatch_hold_reason` 已展示，不崩 |
| `ai_dispatch_enabled` 缺省 | `system_settings` 为 NULL 时回退环境变量初值（默认 false → **走询问**，与 D5 保守口径一致） |
| 用户取消 | `cancel_task` 接受 `requeue_pending`（非终态，可取消） |
| 卡死兜底 | 只扫 `running`，不影响 `requeue_pending` |
| 排队超时告警 | `dashboard_service` 的「排队超时」看 `pending`；`requeue_pending` 属**待人工处理**，纳入同一告警口径（避免静默滞留） |

## 4. 验收标准

- [ ] AI 关闭：节点离线 → 任务 `requeue_pending`，**调度器不派发**，`dispatch_hold_reason` 含询问；
- [ ] AI 开启：节点离线 → 任务 `pending`，调度器下一轮自动派发（现状不回归）；
- [ ] `POST /confirm-redispatch`：`requeue_pending` → `pending`，随后被派发；
- [ ] 非 `requeue_pending` 调用确认 → 409，任务状态不变；
- [ ] `requeue_pending` 任务可被 `PUT /dispatch-preference` 指定节点改派；
- [ ] `requeue_pending` 任务不出现在调度器扫描结果中（防「未确认就自动跑」）；
- [ ] 迁移 `upgrade`/`downgrade` 均可用；
- [ ] 前端：`requeue_pending` 显示「待确认改派」+ 确认按钮，点击后任务回到队列。

## 5. 关联文档

- [ADR-059](../adr/059-scheduling-resilience-and-artifact-sync.md)
- [059-scheduling-resilience-harness.md](./059-scheduling-resilience-harness.md)
- [059-scheduling-resilience-changes.md](./059-scheduling-resilience-changes.md)
