# ADR-066 任务终态自动唤醒编排（task-terminal-orchestrator-wake）

| 项目 | 内容 |
|---|---|
| 状态 | Accepted（2026-10-06 用户裁决：方案 A，核心理念「平台的灵魂是自主性——自己激活、指挥 agent 跑完任务」） |
| 日期 | 2026-10-06 |
| 关联文档 | ADR-033（AI 技能与编排）、ADR-061 M5d（中枢待办）、ADR-064（节点续聊落库模式）、ADR-055（调度韧性） |

## 1. 问题背景（2026-10-06 用户实测）

用户实测「登录页面→部署」多阶段需求，发现任务终态后编排停滞：

- 任务 `7cee1596` 节点侧早已 **completed 100%**（信号已到达中枢状态机），但中枢待办第 4 项
  「跟踪节点执行进度与 T1–T10 完成情况」永远停在 **in_progress**，第 5 项「验收交付物并向用户汇报」永远轮不到。
- 编排 AI 在对话里承诺「节点报完结束后，我会先核对交付物」——**空头支票**：编排 AI 是对话驱动的，
  写完待办对话轮即结束，任务完成时没有任何机制把它叫回来。
- 代码证据：`task_event_handler._handle_task_completed`（:779）只做「改状态机/存摘要/同步产物/记日志」，
  无唤醒逻辑；`AGEN_TASK_COMPLETED` 等终态事件处理路径均无编排联动。

**缺失的闭环**：节点完成 → 中枢状态机更新 ✓；中枢状态机更新 → **编排推进 ✗（本 ADR 补齐）**。

## 2. 决策

任务到达终态（completed / failed）后，中枢**自动唤醒编排 AI** 执行一轮「收尾编排」：
核对交付物 → 推进中枢待办（todo_write）→ 向用户汇报结果 → 若存在下一阶段（复合需求），
列出计划**询问用户确认**（对齐 ADR-033 准则 11「确认后派发」，不自动派发）。

### 2.1 核心设计

1. **触发点**：`_handle_task_completed` / `_handle_task_failed` 落库成功后调用
   `orchestrator_wake_service.schedule_wake(task_id)`（`asyncio.create_task`，fire-and-forget，不阻塞 WS 事件处理）。
2. **唤醒条件**（服务内逐项判断，任一不满足则跳过并记日志）：
   - 开关 `orchestrator_auto_wake_enabled` 开启（system_settings 可热更新，NULL 回退环境变量，默认开——照抄 `ai_dispatch_enabled` 模式）；
   - 任务关联构建会话（`tasks.session_id`）存在且会话行存在——API 直建的无会话任务不唤醒；
   - 任务为 build 模式且终态为 completed/failed（`verify_blocked` 有独立验收决策入口、`review`/`cancelled` 不触发）；
   - **并发保护**：该会话当前有活跃前端编排流（steering active）时延迟重试（10s × 3 次），仍占用则放弃本轮并记审计；
   - **防环限流**：同一根任务链（root_task_id）滑动窗口（24h）内自动唤醒次数 ≤ 5，且连续失败唤醒 ≤ 3 次——
     超限后停止自动唤醒并落审计日志（防止「失败→唤醒→重试→失败」死循环）。
3. **唤醒轮执行**（后台，不依赖前端在线）：
   - 自建 `AsyncSessionLocal`，构造 `OrchestratorService(session)`，注册 `steering_service.open_stream/close_stream`
     （与前端流互斥保护）；
   - 注入**系统触发消息**（role=user，带触发标记，见 §2.2）走既有 `chat_stream` 链路（技能/待办/审计全部复用）；
   - 后台消费事件流（复用 `_persist_chat_exchange` 落库模式）：AI 回复、thinking、steps 全量落库
     `task_building_sessions.messages`——前端打开会话或轮询即见（与 ADR-064 节点回复落库同款时序）。
4. **提示词边界**（触发消息内嵌指令，M3）：
   - 声明本轮为系统自动唤醒（任务终态触发），非用户新需求；
   - 先 `get_task_status` 核对状态与交付物（`change_summary`）；
   - `todo_write` 推进中枢待办：跟踪项→completed，验收/汇报项按实际处理；
   - 用中文向用户汇报（成功=交付物+修改摘要；失败=原因+建议下一步）；
   - 存在下一阶段（如部署）时列出计划**询问用户确认**——未经确认禁止 `create_task`/`smart_dispatch`；
   - 禁止对已完成任务重复派发（对齐 dispatch_skill 的 assigned/running 友好提示）。

### 2.2 触发消息模板（写入会话的 user 消息）

```
[系统自动唤醒] 任务「{title}」已{completed|failed}（ID {task_id 前 8 位}）。
节点回报：{change_summary | 失败原因}。
请：1) 核对任务状态与交付物；2) 推进中枢待办；3) 向用户简要汇报结果；
4) 若存在下一阶段计划，列出并等待用户确认，未经确认不要创建或派发新任务。
```

## 3. 备选方案

| 方案 | 说明 | 结论 |
|---|---|---|
| **A. 终态事件自动唤醒编排 AI**（本决策） | 事件驱动、即时、AI 语义化核对与汇报 | ✅ 采用 |
| B. 轮询心跳唤醒 | 调度器周期扫描「终态但待办未推进」的任务 | ✗ 延迟高（分钟级）、空转查询多；事件已在终态钩子处天然可得 |
| C. 纯规则引擎推进待办（不走 AI） | 代码直接把「跟踪执行」类待办标完成 | ✗ 无法核对交付物语义、无法汇报、无法规划下一阶段；作为 A 的辅助（AI 判断）保留 |
| D. 前端通知 + 用户手动触发 | 零后端改动 | ✗ 不满足用户对自主性的核心诉求 |

## 4. 影响

- **新增**：`orchestrator_wake_service`（唤醒调度/限流/后台轮）；`system_settings.orchestrator_auto_wake_enabled` 列
  （Alembic 迁移）；settings 端点与设置页开关；触发消息模板与唤醒审计日志。
- **复用不动**：chat_stream 技能链、todo_write、_persist_chat_exchange 落库、steering 互斥、dispatch 确认语义。
- **风险与对策**：
  - 唤醒轮失败循环 → 同链限流（§2.1-4）+ 审计；
  - 与用户对话互踩 → steering 活跃检测 + 延迟重试；
  - LLM 配置缺失/调用失败 → 唤醒轮静默降级（记 warning，不影响任务状态机）；
  - 用户不想要自主推进 → 设置页开关一键关闭。
- **明确不做**：唤醒轮不自动派发新任务（下一阶段必须用户确认，对齐准则 11）；不触碰 verify_blocked/review 流程。

## 5. 实际实施情况

（开发完成后回填）
