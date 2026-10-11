# ADR-020：Agent 节点应用内断线重连与任务对账机制

## 状态

Accepted

## 日期

2026-09-03

## 问题背景

当前 Agent 节点与中枢的 WebSocket 连接断开后（如中枢重启、网络抖动），Agent 进程直接退出
（`hub_client.py` 中 `handle_messages` 末尾 `raise RuntimeError`），重连完全依赖 systemd
`Restart=always` 拉起进程。该设计存在以下问题：

1. **开发环境（Windows/WSL 手动运行）断线即永久离线**：进程退出后无人拉起，中枢恢复后节点
   不会自动重连。
2. **systemd 启动限流风险**：unit 未配置 `StartLimitIntervalSec=0`，中枢长时间离线时
   `register()` 快速失败可能触发 `StartLimitBurst`（默认 10 秒内 5 次）后 systemd 放弃拉起。
3. **断网超过心跳超时（30 秒）后的任务冲突**：中枢心跳监控会将离线节点的
   `assigned/running` 任务重置为 pending 并可能改派其他节点，而原节点上 tilacode 仍在执行该
   任务，导致任务被重复执行。
4. **断线期间任务终态上报丢失**：节点在断线期间完成任务时，`agent.task_completed` 等事件
   发送失败被静默丢弃，工作成果从中枢视角丢失。

核心诉求：**无论哪一方离线，连接都应自动恢复**；中枢离线时子 Agent 不退出进程，按固定间隔
重试重连；网络恢复后连接必须能重建，且不能因断线产生任务状态混乱。

## 决策

### 1. Agent 侧应用内重连循环（替代「进程退出 + systemd 重启」）

- `HubClient` 新增 `run_forever()` 会话循环：每轮会话 = `register()`（幂等，token 不覆盖）
  → `connect_websocket()` → 启动心跳/消息协程 → 等待任一协程结束（视为断线）。
- 断线后不退出进程，等待 `RECONNECT_INTERVAL_SECONDS`（默认 5 秒，Pydantic Settings 管理，
  最小值约束 `ge=1`）后重试，直到 `close()`（SIGTERM/SIGINT）才停止。
- 每轮会话结束在 `finally` 中取消未完成协程并清理连接引用，防止协程/句柄泄漏。
- 重连成功后回调 `on_session_ready`：冲刷缓冲事件 → 恢复状态上报（执行中任务上报
  busy + current_task_id，否则转 idle）。
- tilacode/桥接层只启动一次，与连接会话解耦：连接循环先启动，tilacode 并行就绪后才允许
  接单（`task_bridge` 为 None 时派发会被拒绝，天然安全）。

### 2. 断线期间生命周期事件缓冲

- 断线期间 `agent.task_accepted` / `agent.task_completed` / `agent.task_failed` 事件
  进入内存缓冲队列（上限 100 条），重连成功后按序补报。
- `agent.task_progress` 不缓冲（断线期间进度已过时，重连后下一次进度上报自然恢复）。
- 中枢 `task_event_handler._get_verified_task` 的归属校验保证过期补报不会污染已改派的任务。

### 3. 重连后任务对账（新增下行事件 `hub.task_cancel`）

中枢在处理心跳时，若 payload 携带 `current_task_id`，按以下规则对账：

| 任务状态 | 中枢动作 |
|----------|----------|
| 正常归属该节点 | 无需处理 |
| `pending` 且未改派（断线期间被心跳监控重置） | **恢复归属**：`assigned_node_id` 重新指回该节点、状态置 `running`（保留断线期间完成的工作，避免重复执行） |
| 已改派其他节点 / 已终态（completed/failed） | 推送 `hub.task_cancel {task_id, reason}`，节点本地 `TaskBridge.interrupt()` 中断执行 |
| 任务不存在 | 推送 `hub.task_cancel` 通知节点 |

### 4. systemd 兜底（保留外部拉起作为最后一道防线）

- unit 追加 `StartLimitIntervalSec=0`，防止崩溃风暴触发限流后永久放弃。
- 应用内重连生效后，systemd 仅在进程自身崩溃（非断线）时拉起。

## 备选方案

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| 方案 A（本决策）：应用内重连 + 事件缓冲 + 任务对账 | 覆盖所有运行方式；断线期间工作可保留；对账防重复执行 | Agent 侧改动较大，需新增对账事件 | ✅ 选中 |
| 方案 B：仅 systemd 兜底（现状增强） | 改动极小（加 StartLimitIntervalSec=0） | 开发环境/容器场景不生效；断线期间完成任务丢失；任务重复执行无解 | ❌ 否决 |
| 方案 C：断线即退进程 + 前端/中枢主动探测拉起 | 无 | 复杂度更高且仍依赖部署环境 | ❌ 否决 |

## 影响

### 对 Agent 节点的影响

- `hub_client.py` 重构会话生命周期：`handle_messages` 断线返回而非抛错退出进程；新增
  `run_forever` / `_run_session` / `_cleanup_session` / 事件缓冲 / `restore_session_state`。
- `main.py` 启动顺序调整：重连循环先启动，tilacode 并行就绪后通过 `bridge_ready` 事件联动
  上报 idle。
- `config.py` 新增 `RECONNECT_INTERVAL_SECONDS` 配置。
- 新增处理下行事件 `hub.task_cancel`。

### 对中枢的影响

- `_handle_heartbeat` 增加任务归属对账逻辑（每次心跳携带执行中任务 ID 时多一次主键查询，
  开销可忽略）。
- `_mark_node_online` 中无条件打印的 `agent_reconnected` 日志移除（与端点层分支日志重复，
  且首次连接语义失真）。

### 对协议的影响

- 新增中枢 → Agent 下行事件 `hub.task_cancel`（payload：`task_id`、`reason`），同步更新
  `api-standards.md` §4.2 与 `004-agent-node-protocol-spec.md`。

### 已知限制

- 节点在断线期间**已完成**的任务，若中枢在重连前已将其重置为 pending 且未改派，补报的
  `completed` 事件会因归属校验失败被拒（归属信息在重置时被清除，中枢无法区分「待改派的
  旧任务」与「新任务」）。该窗口 = 断线时长 ∩ 任务完成时刻，发生率低； ongoing 任务的
  对账恢复已覆盖主要风险。后续可通过任务表增加 `last_released_node_id` 字段彻底解决。

## 关联文档

- [020-agent-node-reconnection-changes.md](../spec/020-agent-node-reconnection-changes.md)
- [004-agent-node-protocol-spec.md](../spec/004-agent-node-protocol-spec.md)
- [ADR-006](./006-agent-node-deployment.md)（部署接入方式，本决策不改变其结论）
- [ADR-012](./012-tilacode-execution-layer-integration.md)（任务桥接层，`interrupt` 复用）
- [ADR-015](./015-task-stale-detector.md)（心跳监控重置任务的既有行为，对账逻辑与其衔接）
