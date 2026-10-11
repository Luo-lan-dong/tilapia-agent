# 调度准入与产物韧性 Changes 变更清单

> 关联：[ADR-059](../adr/059-scheduling-resilience-and-artifact-sync.md) · [Spec](./059-scheduling-resilience-spec.md) · [Harness](./059-scheduling-resilience-harness.md)
> 规则：边开发边实时登记（修/增/减 + 函数级明细 + 作用说明），**禁止开发完成后一次性补记**。
> 范围约束：覆盖 S1（百分比判据）、S2（拒单原因不可见）、S3（AI 只能选残集）、S4（执行中产物不同步）。
> **不含** S3 演进的「写即推送」准实时同步（`agent.file_changed`，本次仅登记）。

## 计划变更点（编码前骨架）

### A. 中枢（`opc-central-dispatch-hub`）

| # | 文件 | 计划变更类型 | 计划函数/类级明细 | 作用说明 | 缺陷 |
|---|------|------------|-----------------|---------|------|
| A1 | `app/core/config.py` | 增 | `dispatch_min_available_memory_gb`(1.0)、`dispatch_max_load_score`(80.0) | 双门槛阈值（ADR-059 D1） | S1 |
| A2 | `app/core/config.py` | 修 | `dispatch_cpu_threshold_percent`/`dispatch_mem_threshold_percent` 注释改为「metrics 缺失时的兜底」 | 语义澄清，避免双重标准 | S1 |
| A3 | `app/services/dispatch_decision_service.py` | 增 | `NodeEligibility`（eligible/blocking/available_gb/load_score）、`evaluate_eligibility(node)` | 统一双门槛判定与缺口描述 | S1/S3 |
| A4 | `app/services/dispatch_decision_service.py` | 修 | `list_available_nodes` 改为「在线+槽位+未回避」返回全部候选并附 eligibility；新增 `list_autonomous_nodes` | 候选不再被门槛静默剔除（S3 根因） | S1/S3 |
| A5 | `app/services/scheduler.py` | 修 | `_classify_pending_tasks` 三路分类改按 eligibility；场景一/场景二分支 | 无自主候选→询问；指定节点→排队重扫直达 | S3 |
| A6 | `app/services/scheduler.py` | 增 | `_handle_specified_target`、`_emit_dispatch_hold`、强制派送审计 | 指定节点排队与强行派送（唯一例外） | S3 |
| A7 | `app/models/task.py` | 增 | `target_node_id`、`last_reject_reason`、`dispatch_hold_reason`、`forced_dispatch` | 排队/拒单/审计数据 | S2/S3 |
| A8 | `alembic/versions/20260928_scheduling_resilience.py` | 增 | `upgrade`/`downgrade` | 迁移（含回滚） | S2/S3 |
| A9 | `app/services/task_event_handler.py` | 修 | `_handle_task_rejected` 落库 `last_reject_reason` | 拒单原因可见（S2） | S2 |
| A10 | `app/services/task_event_handler.py` | 增 | 执行中增量同步调度（每 30s，在跑任务差集拉取） | 离线丢失窗口 ≤30s（D4） | S4 |
| A11 | `app/services/artifact_service.py` | 增 | `sync_incremental(task_id, node_id, files)`、快照读写 | 增量同步实现（复用 `fetch_file`） | S4 |
| A12 | `app/services/node_heartbeat.py` | 修 | 离线重置任务后按 `ai_dispatch_enabled` 触发「改派+告知」或「询问」 | D5 先判后告/先问后派 | S4 |
| A13 | `app/schemas/task.py` | 修 | `TaskResponse` 暴露新字段 | 前端展示 | S2/S3 |

### B. 节点（`opc-agent-programming`）

| # | 文件 | 计划变更类型 | 计划函数/类级明细 | 作用说明 | 缺陷 |
|---|------|------------|-----------------|---------|------|
| B1 | `app/client/hub_client.py` | 修 | `_resource_busy` 改「可用内存 > 1G 且 load_score < 80」，保留两样本防抖 | 与中枢同口径，消除错配（S1） | S1 |
| B2 | `app/client/hub_client.py` | 修 | 拒单日志补 `available_gb`/`load_score`/阈值 | 拒单可诊断（S1/S2） | S1/S2 |
| B3 | `app/utils/monitor.py` | 修 | 暴露 `available_gb` 与 `load_score` 便捷读取（若缺失） | 自评取数 | S1 |

### C. 前端（`opc-web-console`）

| # | 文件 | 计划变更类型 | 计划函数/类级明细 | 作用说明 | 缺陷 |
|---|------|------------|-----------------|---------|------|
| C1 | `src/types/task.ts` | 修 | `Task` 增 `target_node_id`/`last_reject_reason`/`dispatch_hold_reason`/`forced_dispatch` | 类型对齐 | S2/S3 |
| C2 | `src/components/TaskSummary.tsx` | 修 | 排队态展示等待节点与缺口数值 | 排队可见（S2） | S2 |
| C3 | `src/components/TaskDetailInfo.tsx` | 修 | 展示拒单原因 | 拒单可见（S2） | S2 |
| C4 | `src/components/TaskSummary.tsx`（或详情） | 增 | `failed`/离线任务「改派」入口 | 失败可改派（S4） | S4 |

### D. 文档与规范

| # | 文件 | 计划变更类型 | 明细 |
|---|------|------------|------|
| D1 | `docs/adr/059-scheduling-resilience-and-artifact-sync.md` | 增 | ADR 全文（已完成） |
| D2 | `docs/spec/059-scheduling-resilience-spec.md` | 增 | Spec 全文（已完成） |
| D3 | `docs/spec/059-scheduling-resilience-harness.md` | 增 | Harness 全文（已完成） |
| D4 | `docs/spec/059-scheduling-resilience-changes.md` | 增 | 本文件 |
| D5 | `docs/INDEX.md` | 修 | 1.1/1.2/1.3/六 四处登记 |
| D6 | `docs/adr/032-concurrent-task-scheduling.md` | 修 | 补修订记录（判据与拒单语义变更） |
| D7 | `docs/adr/028-task-workspace-binding-and-artifact-sync.md` | 修 | 补修订记录（「留作后续演进」的准实时同步以增量拉取形式落地；S3 仍登记不做） |
| D8 | `docs/standards/api-standards.md` | 修 | 新增排队/询问事件与任务字段说明 |
| D9 | `docs/standards/agent-node-standards.md` | 修 | 节点自评口径（可用内存+负载） |

### E. 测试

| # | 文件 | 计划变更类型 | 明细 | 对应用例 |
|---|------|------------|------|---------|
| E1 | `opc-central-dispatch-hub/tests/services/test_dispatch_decision.py` | 增 | eligibility 双门槛与边界 | UT-01~UT-09 |
| E2 | `opc-central-dispatch-hub/tests/services/test_scheduler*.py` | 增 | 场景一/二分支、指定节点直达、强行派送 | UT-10~UT-18 |
| E3 | `opc-central-dispatch-hub/tests/services/test_task_event_handler.py` | 增 | 拒单原因落库、排队原因 | UT-19~UT-22 |
| E4 | `opc-central-dispatch-hub/tests/services/test_artifact_incremental.py` | 增 | 差集与增量拉取 | UT-23~UT-30 |
| E5 | `opc-agent-programming/tests/test_hub_client.py` | 增 | 自评双门槛与日志读数 | UT-31~UT-33 |
| E6 | `opc-web-console/src/__tests__/components/TaskSummary.test.tsx` | 增 | 排队/拒单展示与改派入口 | UT-34~UT-36 |

## 变更清单（实际实施明细）

> 边开发边登记；每条完成后立即追加。函数名以实际落地为准（与上表计划的差异在此处如实记录）。

### 中枢（`opc-central-dispatch-hub`）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/core/config.py` | 增 | `dispatch_min_available_memory_gb`(默认 1.0)、`dispatch_max_load_score`(默认 80.0)、`dispatch_hold_notify_interval_seconds`(默认 120) | 双门槛阈值 + 排队提示节流（ADR-059 D1/D3） |
| `app/models/task.py` | 增 | `target_node_id`(FK+索引)、`last_reject_reason`(String64)、`dispatch_hold_reason`(String200)、`forced_dispatch`(Boolean NOT NULL default false) | 排队/拒单/审计数据（D2/D3） |
| `alembic/versions/20260928_scheduling_resilience.py` | 增 | `upgrade`/`downgrade`（4 列 + FK + 索引，可回滚） | 迁移 |
| `app/services/dispatch_decision_service.py` | 增 | `node_load_score`、`node_available_memory_gb`、`is_node_eligible`、`eligibility_gap`、`list_eligible_nodes`、`running_task_counts`、`diagnose_eligibility`、`build_hold_reason`、`pick_lowest_load_eligible` | 统一双门槛判定、缺口描述、候选诊断（D1/D3） |
| `app/services/dispatch_decision_service.py` | 修 | `build_candidate_snapshot` 增 `available_memory_gb`/`load_score`/`eligible`；`_DECISION_SYSTEM_PROMPT` 要求优先选 `eligible=true` | AI 决策看得见准入状态（D1） |
| `app/services/scheduler.py` | 修 | `_classify_pending_tasks` → 四路（指定节点 / 快速路径 / AI / 规则）+ holds；`_assign_plan` 内的 `_assign_task_to_node` 清空排队原因并记录 `task_forced_dispatched`；`_quick_path_decision` 支持自定义 reason；`dispatch_pending_tasks` 落库 holds | 指定即承诺、无合格节点不静默（D2/D3） |
| `app/services/scheduler.py` | 增 | `_resolve_target_task`、`_apply_holds`、`_hold_notified_at`（进程内节流） | 指定节点排队复检 + 排队原因落库/告警节流（D2/D3） |
| `app/services/scheduler.py` | 修（登记纠偏） | 计划中的 `_emit_dispatch_hold` 事件广播**未实现**：中枢无任务级 SSE 总线，改为「落库 `dispatch_hold_reason` + 结构化日志」，前端经任务列表/详情与 `GET /{task_id}/eligibility` 展示 | 如实记录实现方式差异 |
| `app/services/task_event_handler.py` | 修 | `_handle_task_rejected` 落库 `last_reject_reason` + `dispatch_hold_reason`（含节点名与回避说明）；`_handle_task_accepted` 清空两者 | 拒单原因可见（D3） |
| `app/services/task_event_handler.py` | 增 | `_node_display_name`、`_schedule_incremental_sync`（含 `_incremental_sync_inflight` 单飞）、`_is_task_in_flight`；`_handle_file_tree` 在途任务触发增量同步 | 执行中增量同步（D4） |
| `app/services/artifact_service.py` | 增 | `get_synced_state`、`_save_synced_state`、`select_changed_files`、`sync_incremental`、常量 `MAX_INCREMENTAL_FILES_PER_CYCLE`(20)、`MAX_INCREMENTAL_FILE_BYTES`(2MB) | 增量同步实现（复用 `fetch_file`）；单文件失败隔离（D4） |
| `app/services/node_heartbeat.py` | 修 | `reset_tasks_for_offline_nodes` 重置任务时写入 `dispatch_hold_reason`（离线重新排队可见） | D5 可见性（**登记差异**：见下） |
| `app/schemas/task.py` | 修/增 | `TaskResponse` 增 `target_node_id`/`target_node_name`/`last_reject_reason`/`dispatch_hold_reason`/`forced_dispatch`（含 NULL 归一校验器）；增 `TaskDispatchPreferenceRequest`、`TaskEligibilityCandidate`、`TaskEligibilityResponse` | 前端展示 + 偏好写入契约（D2/D3） |
| `app/services/task_service.py` | 修 | `list_tasks`/`get_task` 双别名 JOIN 取 `assigned_node_name` + `target_node_name`；`_build_task_response` 增 `target_node_name`；`dispatch_task` 增 `force` 参数并在未达标时排队等待指定节点 | 指定节点队列语义（D2） |
| `app/services/task_service.py` | 增 | `set_dispatch_preference`（指定节点/强行派送，显式指定即清除回避）、`get_dispatch_eligibility`（候选缺口 + 下一步建议） | 用户/AI 偏好入口与排队诊断（D2/D3） |
| `app/api/v1/endpoints/tasks.py` | 增 | `PUT /{task_id}/dispatch-preference`、`GET /{task_id}/eligibility` | 偏好与诊断 API |
| `app/skills/dispatch_skill.py` | 修 | 参数增 `target_node_name`/`force`；候选改用 `list_eligible_nodes`；增 `_find_node_by_name`、`_dispatch_to_named_node` | **AI 偏好真正决定选择**（点名节点＝等待该节点，force 才越门槛）（D2） |

### 节点（`opc-agent-programming`）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/utils/monitor.py` | 增 | `get_available_memory_gb()`、`get_load_score()`、模块级 `_last_load_score`（`collect_metrics` 内缓存） | 自评取数与中枢同口径（D1） |
| `app/client/hub_client.py` | 修 | `_resource_busy` 改双门槛（`available_memory_gb > 1.0` 且 `load_score < 80`，保留两次采样防抖；`load_score` 缺失回退 CPU 百分比）；常量 `_MIN_AVAILABLE_MEMORY_GB`/`_MAX_LOAD_SCORE`；拒单日志补读数 | 消除「中枢放行、节点拒单」错配（D1/D3） |

### 前端（`opc-web-console`）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `src/types/task.ts` | 修 | `Task` 增 `target_node_id`/`target_node_name`/`last_reject_reason`/`dispatch_hold_reason`/`forced_dispatch` | 类型对齐 |
| `src/components/TaskTable.tsx` | 修 | 「分配节点」列：pending 且有排队原因时显示「排队中 / 等待 X」并按需 tooltip 展示完整缺口原因（列宽 140→180） | 排队可见（D3） |
| `src/components/TaskDetailInfo.tsx` | 修 | 新增「指定节点」（含强行派送标记）、「排队原因」、「最近拒单」三项 | 详情可见（D3） |

### 测试

| 文件 | 变更类型 | 明细 |
|------|---------|------|
| `opc-central-dispatch-hub/tests/services/test_ai_dispatch_decision.py` | 增 | 9 个准入判定单测（双门槛、边界 1.0/80.0、绝对内存 vs 百分比、旧节点兼容、类型容错、hold reason、规则回退、slot 满诊断、eligible 预筛）+ 候选快照字段断言更新 |
| `opc-central-dispatch-hub/tests/services/test_artifact_incremental_sync.py` | 增 | 11 个增量同步单测（首轮全选、未变跳过、size/mtime 变更、缓存丢失重取、越界/超大过滤、单轮上限、状态持久化、单文件失败隔离、无节点 no-op、状态损坏容错、清单/指纹分文件） |
| `opc-agent-programming/tests/test_hub_client.py` | 修 | 4 个旧用例（仅 patch `get_resource_usage`，已退化为假通过）重写为按新双门槛 patch；新增 6 例（双达标放行、内存不足、负载 ≥80、边界值、绝对内存 vs 百分比、load_score 缺失 CPU 兜底） |
| `opc-web-console/src/__tests__/components/TaskTable.test.tsx` | 增 | 4 例排队可见性（排队中+目标节点、tooltip 缺口、无原因占位符、running 优先显示分配节点） |

### 登记差异（与计划不一致处，如实记录）

| 计划项 | 实际实现 | 原因 |
|-------|---------|------|
| A3 `NodeEligibility` 数据类 + `evaluate_eligibility` | 改为 `is_node_eligible` / `eligibility_gap` 两个纯函数 | 无需引入新数据类；布尔 + 缺口文本即可覆盖调度、诊断、前端三处需要，且避免既有调用方大范围改动 |
| A4 `list_available_nodes` 改为返回全部候选；新增 `list_autonomous_nodes` | `list_available_nodes` 语义不变（保留百分比预筛兜底）；新增 `list_eligible_nodes` 作为其上的准入收窄 | 保持旧节点（无 metrics）兼容与既有 API 行为不回归；`list_eligible_nodes` 即 AI 决策候选域，等价达成「候选不再被静默剔除」的目标 |
| A6 `_emit_dispatch_hold` 事件 | 未实现，改为落库 + 日志 + 查询接口 | 中枢缺少任务级事件总线；新增总线超出本次范围，且落库对前端更可靠（刷新不丢） |
| A10「每 30s 定时器增量同步」 | 事件驱动：复用节点已每 30s 上报的 `agent.file_tree` 触发差集拉取（同任务单飞） | 效果等价（节拍由节点侧 30s 上报决定），省去中枢定时器与任务态扫描；失败/退出不阻塞事件处理 |
| A12 离线按 `ai_dispatch_enabled` 分流「改派+告知」/「询问」 | **已实现（第三批）**：新增 `requeue_pending` 待确认挂起态，`reset_tasks_for_offline_nodes` 按开关分流 | 原为未完成项，现已闭环 |
| C2/C4 `TaskSummary` 排队展示与「改派」入口 | **已实现（第三批）**：派发弹窗新增「无视资源水位强行派送」与准入缺口提示；`requeue_pending` 增加「确认改派」按钮 | 原为范围收敛，现已补齐 |

### 未完成项（必须显式标注）

1. ~~**D5「AI 关闭时先询问」交互**~~ → **已实现（第三批）**，见
   [d5-ask-branch.md](./059-scheduling-resilience-d5-ask-branch.md)。仅余「中枢主动向会话推送询问」
   未做（以 `dispatch_hold_reason` 落库 + 前端展示 + 会话内可读替代）。
2. **D5「改派不重生成三件套」的端到端验证**：单元层已锁定（`test_failed_task_can_be_redispatched_without_regenerating_docs` 断言 `specification` 逐字不变、`dispatch_type` 恢复 auto），但**未在真实库做 IT/E2E**（IT-4）。
3. ~~**前端「强行派送 / 指定节点」按钮**~~ → **已实现（第三批）**。

### 补修记录（2026-09-28 第三批：B4 卫生项 + B2 前端控件 + B1 D5 询问分支）

**B4 三条卫生项**

| 文件 | 变更类型 | 明细 |
|------|---------|------|
| `app/services/dispatch_backoff.py` | 增 | `prune_expired(now=None) -> int` 全局清收过期回避记录；`excluded_nodes` 补 docstring 说明「只清本任务」的局限 |
| `app/services/scheduler.py` | 修 | 调度循环每轮调用 `dispatch_backoff.prune_expired()` 并记 `dispatch_backoff_pruned`（取消/卡死/长期 pending 等不触发 `clear_rejections` 的路径统一兜底） |
| `app/services/dispatch_decision_service.py` | 增 | `blocking_reasons(node) -> list[str]` 并列给出**全部**缺口；`node_cpu_percent(node)` 读数 helper |
| `app/services/dispatch_decision_service.py` | 修 | `eligibility_gap` 补 CPU 兜底分支；`is_node_eligible` 增「`load_score` 缺失 → CPU 百分比兜底」；`diagnose_eligibility` 返回值增 `blocking_reasons`；`build_hold_reason` 用「/」并列同节点多缺口 |
| `app/schemas/task.py` | 修 | `TaskEligibilityCandidate` 增 `blocking_reasons: list[str]` |
| `app/services/task_service.py` | 修 | `get_dispatch_eligibility` 透传 `blocking_reasons` |

**B2 前端控件（含一处谎报成功缺陷修复）**

| 文件 | 变更类型 | 明细 |
|------|---------|------|
| `app/schemas/task_dispatch.py` | 修 | `TaskDispatchRequest` 增 `force`；`TaskDispatchResponse` 增 `queued_on_node`/`hold_reason` |
| `app/api/v1/endpoints/task_dispatch.py` | 修 | 透传 `force`，响应回填排队信息，日志记 `forced_dispatch`/`queued_on_node` |
| `web/src/api/tasks.ts` | 修/增 | `dispatchTask` 增 `force`；新增 `setDispatchPreference`/`confirmRedispatch`/`fetchTaskEligibility` 及响应类型 |
| `web/src/components/DispatchEligibilityHint.tsx` | 增 | 派发准入提示：hint + 所选节点读数 + **全部**缺口清单 |
| `web/src/components/TaskDetailModals.tsx` | 修 | 派发弹窗拉取准入诊断、展示缺口、新增「无视资源水位强行派送」勾选（每次打开重置，防上次越权选择被沿用） |
| `web/src/hooks/useTaskPlanActions.ts` | **修缺陷** | `handleManualDispatch` 原先**无论后端返回什么都写 `status='assigned'` 并提示「已指派」**。ADR-059 后节点不达标会改为排队 → 界面**谎报成功**。改为按 `queued_on_node`/`status` 分流：排队时保持 `pending` 并记录 `target_node_id`/`dispatch_hold_reason`，warning 提示真实原因 |
| `web/src/components/TaskTable.tsx` | 修 | 分配节点列支持 `requeue_pending`（「待确认」+ 等待确认改派） |
| `web/src/components/TaskDetailInfo.tsx` | 修 | 状态标签补 `review`/`requeue_pending` |

**B1 D5「先询问」分支**

| 文件 | 变更类型 | 明细 |
|------|---------|------|
| `docs/spec/059-scheduling-resilience-d5-ask-branch.md` | 增 | 落地设计（挂起态、分流、确认入口、兼容性、验收） |
| `app/models/task.py` | 增 | `TaskStatus.REQUEUE_PENDING`；CHECK 约束扩值；常量 `DISPATCHABLE_STATUSES`/`ACTIVE_STATUSES` |
| `alembic/versions/20260928b_requeue_pending.py` | 增 | 迁移：重建 status CHECK（downgrade 先把新值归位再收紧约束） |
| `app/services/node_heartbeat.py` | 修 | `reset_tasks_for_offline_nodes` 按 `ai_dispatch_enabled` 分流（开启→`pending` 自动改派；关闭→`requeue_pending` 等确认） |
| `app/services/scheduler.py` | 修 | 派发扫描改用 `Task.status.in_(DISPATCHABLE_STATUSES)`——**显式排除** `requeue_pending` |
| `app/services/task_service.py` | 增 | `confirm_redispatch`（`requeue_pending`→`pending`，清排队原因与回避记录；非该状态 409 幂等保护） |
| `app/api/v1/endpoints/tasks.py` | 增 | `POST /{task_id}/confirm-redispatch` |
| `web/src/types/task.ts` | 修 | `TaskStatus` 增 `requeue_pending` |
| `web/src/components/TaskDetailDrawer.tsx` | 修 | `requeue_pending` 时展示「确认改派」按钮（并允许直接指定节点改派） |
| `web/src/components/dashboard/DashboardTaskTable.tsx` | 修 | 状态映射补 `requeue_pending` |

**测试（第三批新增 28 例）**

| 文件 | 明细 |
|------|------|
| `hub/tests/services/test_dispatch_backoff.py` | 8 例：TTL 语义、只清本任务、全局清收、保留未过期、部分过期、空字典、缺省时钟 |
| `hub/tests/services/test_ai_dispatch_decision.py` | 5 例：UT-04 全部缺口、可自主无缺口、UT-08 CPU 兜底、指标全缺放行、内存门槛优先 |
| `hub/tests/services/test_node_heartbeat.py` | 7 例：AI 开启自动改派、AI 关闭待确认、**requeue_pending 不被派发**、确认回队列、错误状态 409、可取消、可指定节点改派 |
| `web/src/__tests__/components/TaskDetailModals.test.tsx` | 4 例：全部缺口展示、force=false、force=true、诊断失败降级 |
| `web/src/__tests__/hooks/useTaskPlanActions.test.tsx` | 5 例：派送成功、**排队不谎报成功**、force 载荷、forced_dispatch 记录、失败不改状态 |
| `web/src/__tests__/components/TaskDetailDrawerRequeue.test.tsx` | 4 例：待确认展示、非该状态不显示按钮、确认成功刷新、确认失败可重试 |

### 补修记录（2026-09-28 二次走查）

| 文件 | 变更类型 | 明细 |
|------|---------|------|
| `app/services/task_service.py` | 修（**修缺陷**） | `set_dispatch_preference` 原先对 `failed` 直接抛 409，与「失败任务可改派」的用户要求及自身 docstring 矛盾。改为：`completed` 才拒绝；`running`/`assigned` 拒绝并提示先取消；**`failed` 允许改派** → 回 pending + 恢复 `dispatch_type=auto` + 重算派发窗口 + `progress=0`，`specification`/`change_summary` 原样保留（复用文档，不重生成三件套） |
| `app/api/v1/endpoints/tasks.py` | 修 | `PUT /{task_id}/dispatch-preference` 的 summary/description 同步写明「失败改派」与状态边界 |
| `tests/services/test_dispatch_preference.py` | 增 | 7 个用例：指定节点排队、**失败任务改派且三件套逐字不变**、completed 拒绝、running 拒绝、force 落审计、清除指定、显式指定清除回避 |

## 登记记录

- 2026-09-28：创建骨架（ADR-059 四件套建立；Spec/Harness 同步完成，未写生产代码）。
- 2026-09-28：范围经用户确认——准入判据用「可用内存 >1G **且** `load_score` <80，
  **任一不满足即需询问**」；调度器 10s 一轮（现状即 10s，无需改）；
  产物同步采用 **S2 增量同步**，**S3（写即推送）登记为后续演进**；
  节点离线改派策略：**AI 智能调度开启 → 先改派再告知；未开启 → 先询问**；
  指定节点排队不绕过阈值，**唯一例外是用户明确要求强行派送**。
- 2026-09-28：节点 D1 落地并验证——`monitor.get_available_memory_gb/get_load_score` +
  `hub_client._resource_busy` 双门槛；`tests/test_hub_client.py` 旧 4 例被识别为**假通过**
  （只 patch `get_resource_usage`，新实现根本不读它）并重写，新增 6 例；节点全量 `pytest` 182 passed。
- 2026-09-28：中枢 D1/D2/D3/D4 落地——config 三阈值、task 四列 + 迁移、判定/诊断/排队原因纯函数、
  调度器四路分类 + holds、拒单原因落库、增量同步（`select_changed_files`/`sync_incremental`）、
  偏好 API 与诊断 API、`smart_dispatch` 技能支持点名节点与 force；新增 20 个单测全通过。
- 2026-09-28：前端 D3 落地——类型对齐、`TaskTable` 排队列、`TaskDetailInfo` 三项；`tsc --noEmit` 通过，
  `TaskTable` 8 例通过（含 4 例新增）。
- 2026-09-28：**未完成项 1~3 已在上表显式登记**（D5 询问分支 / 不重生成回归 / 前端控件），
  避免「文档声称已修」与实现不符。
