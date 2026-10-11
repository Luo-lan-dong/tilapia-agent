# 调度准入与产物韧性 Harness（双门槛 + 排队重扫 + 增量同步 + 故障改派）

> 关联：[ADR-059](../adr/059-scheduling-resilience-and-artifact-sync.md) · [Spec](./059-scheduling-resilience-spec.md) · [Changes](./059-scheduling-resilience-changes.md)
> 涉及工程：中枢 `opc-central-dispatch-hub/tests`（pytest）、节点 `opc-agent-programming/tests`（pytest）、前端 `opc-web-console/src/__tests__`（vitest）。

## 1. 测试金字塔

```
   E2E（3）        真实场景：指定节点排队后自动派送 / 执行中离线改派 / 强行派送审计
    集成（6）      eligibility 分类 / 无自主候选时询问 / 增量同步闭环 /
                  先判后告 与 先问后派 两条分支 / 迁移动 downgrade
   单元（20+）     双门槛判定与边界 / 缺口描述 / 拒单原因落库 / 排队原因 /
                  file_tree 差集 / 改派不重生成
```

## 2. 单元测试清单

### 2.1 准入双门槛（hub：tests/services/test_dispatch_decision.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-01 | 双门槛都达标 → 可自主 | `available_gb=2.0, load_score=50` → eligible |
| UT-02 | 内存不达标 → 不可自主 | `available_gb=0.9, load_score=50` → not eligible，`blocking="memory"` |
| UT-03 | 负载不达标 → 不可自主 | `available_gb=2.0, load_score=85` → not eligible，`blocking="load"` |
| UT-04 | 两者都不达标 → 不可自主（任一不满足即生效） | `0.5 / 95` → not eligible，`blocking` 同时含两项 |
| UT-05 | 边界值 | `available_gb=1.0`（不大于 1）→ 不可自主；`load_score=80`（不小于 80）→ 不可自主 |
| UT-06 | 绝对内存口径（用户核心诉求） | 4G/82.7%（剩 0.69G）→ 不可自主；8 核/43%（剩 2.28G）→ 可自主 |
| UT-07 | metrics 缺失降级 | 无 `metrics_snapshot` → 回退百分比兜底门槛，不抛错 |
| UT-08 | load_score 缺失但 CPU 可读 | 用 CPU 兜底并在结果中标注口径降级 |
| UT-09 | 缺口描述可读 | 产出含 `available_memory_gb`/`load_score`/`blocking` 的结构，供对话展示 |

### 2.2 派发决策分支（hub：tests/services/test_scheduler*.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-10 | 场景一 · 无 eligible | **不派发**、任务保持 pending、产生询问事件（含候选与缺口） |
| UT-11 | 场景一 · 唯一 eligible | 规则直达，**不调用 LLM**（决策 `decision_source="rule"`） |
| UT-12 | 场景一 · 多 eligible | 调用 AI，且 AI 只能从 eligible 中选（白名单校验） |
| UT-13 | 场景二 · 指定节点达标 | 直接派送给 target，**不调用 LLM** |
| UT-14 | 场景二 · 指定节点不达标 | 保持排队、不派给其他节点、产生缺口提示 |
| UT-15 | 场景二 · target 达标当轮派送 | 调度轮触发后 ≤1 轮完成派送 |
| UT-16 | 改派指定节点 | 更新 target 后可派给新目标 |
| UT-17 | 强行派送（唯一例外） | 仅当 payload 标记强制时越过门槛，且 `forced_dispatch=True` 落库 |
| UT-18 | 非强制时拒绝越过门槛 | 即使 AI 偏好不达标节点，也不派送（回归：偏好不得绕过 D1） |

### 2.3 拒单与排队可见（hub：tests/services/test_task_event_handler.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-19 | 拒单原因落库 | `reason=resource_busy` → `task.last_reject_reason=="resource_busy"`，任务回 pending、`assigned_node_id=None` |
| UT-20 | 旧节点无 reason | 缺字段按 `slots_full` 处理（协议向后兼容不回归） |
| UT-21 | 排队原因写入 | 无 eligible 时 `dispatch_hold_reason` 含缺口摘要 |
| UT-22 | 排队提示事件 | 推送内容含每个候选的 `available_memory_gb`/`load_score`/`blocking` |

### 2.4 增量产物同步（hub：tests/services/test_artifact_incremental.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-23 | 首次快照无差集 | 无上次快照 → 只记录基线，不拉取（避免首轮全量） |
| UT-24 | 差集 = size 变化 | 同路径 size 变化 → 计入待拉取 |
| UT-25 | 差集 = mtime 变化 | size 相同但 mtime 变 → 计入 |
| UT-26 | 新增文件计入、删除文件忽略 | 新路径计入；消失路径从快照移除且不报错 |
| UT-27 | 无变化不拉取 | 快照完全一致 → 零 `file_fetch`（避免空转流量） |
| UT-28 | 单文件拉取失败不阻断 | 一个文件失败 → 其他继续，记 WARNING |
| UT-29 | 缓存落盘路径 | 落 `{artifact_cache_dir}/{task_id}/{相对路径}`，与既有按需拉取同构 |
| UT-30 | 完成后仍走全量 zip | completed 时触发 `fetch_zip`（既有行为不回归） |

### 2.5 节点侧（node：tests/test_hub_client.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-31 | 自评改双门槛 | `available_gb=0.8` → 拒单；`load_score=85` → 拒单；两者达标 → 放行 |
| UT-32 | 拒单日志含读数 | 日志 extra 含 `available_gb`/`load_score`/两个阈值 |
| UT-33 | 连续采样语义保留 | 单次抖动不拒（沿用 ADR-055 的两样本修复） |

### 2.6 前端（web：src/__tests__/components/TaskSummary.test.tsx 等）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-34 | 排队态显示缺口 | `dispatch_hold_reason` 非空 → 展示原因与候选缺口 |
| UT-35 | 拒单原因展示 | `last_reject_reason` 非空 → 摘要/详情可见 |
| UT-36 | 改派入口 | `failed`/离线任务显示改派按钮，点击触发回调 |

## 3. 集成测试

| 编号 | 用例 | 方式 |
|------|------|------|
| IT-1 | 无自主候选 → 询问 → 用户指定 A → A 达标 → 自动派送 | 构造两个不达标节点 + 指定 A；推进调度轮断言派送且未调 LLM |
| IT-2 | 指定 A 期间改派 B | 更新 target 后下一轮派给 B |
| IT-3 | 增量同步闭环 | 模拟执行中两次 `file_tree` 上报（含 size 变化）→ 断言仅拉取差异文件且缓存内容为最新 |
| IT-4 | 离线改派（AI 开启） | 节点离线 → 任务自动改派 + 告知用户；断言未重生成 `specs/` 新版本目录 |
| IT-5 | 离线询问（AI 关闭） | 节点离线 → 无自动改派，产生询问事件；用户确认后才派 |
| IT-6 | 迁移可升级可回滚 | `alembic upgrade head` / `downgrade -1` 均成功；新列存在性断言 |

## 4. E2E

| 编号 | 场景 | 验证 |
|------|------|------|
| E2E-1 | 中枢创建一个任务并指定 `笔记本`；该节点在排队期间不达标 | 任务显示「等待 笔记本（原因：可用内存 0.8G）」；节点恢复后 ≤10s 自动开跑，且对话中未出现 AI 选节点记录 |
| E2E-2 | 任务执行中重启节点进程（模拟离线） | AI 调度开启：任务自动改派到另一节点并复用已同步产物（无需重新生成文档）；关闭：收到询问后确认才改派 |
| E2E-3 | 用户要求「强行派给负载 90 的节点」 | 派送成功且审计记录 `forced_dispatch=true` |

## 5. CI 门禁

- 中枢 `pytest tests/` 全绿；节点 `python -m pytest tests/` 全绿；前端 `tsc -b` + `vitest run` + `vite build` 全过；
- `ruff check` / `ruff format --check` 全过；
- **守卫用例（必须存在，防回归）**：
  - UT-06（绝对内存口径）——防「82.7% 判合规」回潮；
  - UT-04 + UT-18（任一不满足即询问、偏好不得绕过门槛）——防 AI 静默硬塞；
  - UT-10（无自主候选时询问而非静默）——防排队不可见回归；
  - UT-13/UT-15（指定节点达标当轮直达、不经 AI）——防偏好被评分覆盖；
  - UT-27（无变化不拉取）——防增量同步退化为流量风暴；
  - IT-4（改派不重生成三件套）——防与 ADR-012/036 决策冲突回潮；
- **迁移门禁**：本决策包含迁移（新列），必须提供 `upgrade` 与 `downgrade`。

## 6. 实施状态对照（2026-09-28）

> 计划用例与实际落地用例的对照；**未实现项显式标注**，不得据本文件认为已全部覆盖。

### 已落地的守卫用例（实际文件与用例名）

| 计划编号 | 实际位置 | 实际用例 |
|---------|---------|---------|
| UT-01~UT-03、UT-05、UT-06、UT-07、UT-09 | `hub/tests/services/test_ai_dispatch_decision.py` | `test_eligible_true_when_both_gates_pass`、`test_ineligible_when_available_memory_at_or_below_1gb`、`test_ineligible_when_load_score_at_or_above_80`、`test_absolute_memory_not_percentage`、`test_missing_metrics_legacy_node_passes`、`test_metrics_type_tolerance`、`test_build_hold_reason_reports_gap_and_target`、`test_pick_lowest_load_eligible_skips_blocked`、`test_diagnose_eligibility_marks_slots_full`、`test_list_eligible_nodes_filters_by_gate` |
| UT-10 | `hub/tests/services/test_scheduler.py` | `test_no_eligible_node_holds_task_with_reason` |
| UT-13 / UT-15 | 同上 | `test_specified_node_dispatches_when_eligible` |
| UT-14 | 同上 | `test_specified_node_queues_when_not_eligible` |
| UT-17 | 同上 | `test_specified_node_forced_dispatch_bypasses_gate` |
| UT-18 | 同上 | `test_unspecified_task_never_forced_to_unhealthy_node` |
| — | 同上 | `test_forced_dispatch_still_requires_node_online`、`test_specified_node_offline_holds_with_offline_reason`、`test_eligible_multi_node_still_dispatches_lowest` |
| UT-19 / UT-20 | `hub/tests/services/test_task_event_handler.py` | `test_task_rejected_persists_reason_and_hold`、`test_task_rejected_legacy_payload_defaults_to_slots_full`、`test_task_accepted_clears_reject_and_hold_reason` |
| UT-16、UT-18（改派语义） | `hub/tests/services/test_dispatch_preference.py` | `test_set_preference_targets_node_and_requeues`、`test_failed_task_can_be_redispatched_without_regenerating_docs`、`test_completed_task_rejects_preference_change`、`test_running_task_rejects_preference_change`、`test_force_flag_is_recorded_for_audit`、`test_clearing_target_returns_to_regular_prefilter`、`test_preference_clears_rejection_backoff` |
| UT-24~UT-29 | `hub/tests/services/test_artifact_incremental_sync.py` | `test_size_or_mtime_change_is_detected`、`test_unchanged_files_are_skipped`、`test_unsynced_entry_missing_locally_is_reselected`、`test_invalid_and_oversized_entries_are_ignored`、`test_sync_incremental_persists_state`、`test_sync_incremental_isolates_single_file_failure`、`test_sync_incremental_without_node_is_noop`、`test_synced_state_survives_corruption`、`test_manifest_and_state_files_are_separate`、`test_first_cycle_selects_everything`、`test_cycle_limit_caps_selection` |
| UT-31 / UT-33 | `node/tests/test_hub_client.py` | `test_resource_busy_true_when_available_memory_below_1g`、`test_resource_busy_true_when_load_score_at_or_above_80`、`test_resource_busy_false_when_both_gates_pass`、`test_resource_busy_boundary_values`、`test_resource_busy_absolute_memory_not_percentage`、`test_resource_busy_load_score_falls_back_to_cpu`、`test_resource_busy_false_on_single_transient_spike`、`test_resource_busy_true_when_sustained`、`test_resource_busy_false_when_metrics_missing` |
| UT-34 | `web/src/__tests__/components/TaskTable.test.tsx` | `pending 且有排队原因时显示「排队中」与等待的目标节点`、`排队原因作为 tooltip 提供缺口数值`、`无排队原因的 pending 任务仍显示分配节点占位符`、`running 任务即使带排队原因也优先展示已分配节点` |

### 未实现（本次不做，需后续补齐）

| 计划编号 | 说明 |
|---------|------|
| UT-04 | ~~未实现~~ → **已实现（第三批）**：`blocking_reasons` 并列返回全部缺口，用例 `test_blocking_reasons_lists_all_gaps`（`test_ai_dispatch_decision.py`） |
| UT-08 | ~~中枢侧未实现~~ → **已实现（第三批）**：`is_node_eligible`/`eligibility_gap` 在 `load_score` 缺失时回退 CPU 百分比，用例 `test_load_score_missing_falls_back_to_cpu` |
| UT-11 / UT-12 | 「唯一 eligible 不调 LLM」「多 eligible 时 AI 只在 eligible 内选」——既有快速路径/AI 测试覆盖了通用分支，但**未针对双门槛收窄后的候选域新增断言** |
| UT-16 | 「改派指定节点后可派给新目标」——已由 `test_dispatch_preference.py` 覆盖（见上表） |
| UT-21 / UT-22 | 「排队原因写入含缺口摘要」已由调度器用例覆盖（UT-10）；**「排队提示事件含候选明细」未实现**（无事件总线），改由 `GET /{task_id}/eligibility` 提供同样信息 |
| UT-23 | 计划语义为「首轮只记基线不拉取」，**实际实现相反**（首轮拉取全部，才能满足「离线最多丢 30s」的目标），用例 `test_first_cycle_selects_everything` 锁定实际语义 |
| UT-30 | 「completed 仍走全量 zip」——既有行为未改，但**未新增用例**锁定 |
| UT-32 | 「拒单日志含读数与阈值」——代码已按 `available_memory_gb`/`load_score`/`min_available_memory_gb`/`max_load_score` 记录，但**无断言日志 extra 的用例** |
| UT-35 | ~~前端「最近拒单」无单测~~ → **已实现（第三批）**：`DispatchEligibilityHint` 展示全部缺口，用例见 `TaskDetailModals.test.tsx` |
| UT-36 | ~~「改派入口」按钮未实现~~ → **已实现（第三批）**：派发弹窗「无视资源水位强行派送」+ `requeue_pending` 的「确认改派」按钮，用例见 `TaskDetailModals.test.tsx` / `TaskDetailDrawerRequeue.test.tsx` |
| IT-1~IT-6 | **全部未实现**（无集成层闭环用例）：含最关键的两条——IT-4「改派不重生成三件套」（单元层已锁定 `specification` 逐字不变）与 IT-5「AI 关闭时先询问」（**功能已实现**，用例见 `test_node_heartbeat.py` 的 7 例，但非真实库集成） |
| E2E-1~E2E-3 | **全部未实现**（需两只真实节点 + 重启进程，属人工验收） |

### D5「先询问」分支（第三批，设计见 [d5-ask-branch.md](./059-scheduling-resilience-d5-ask-branch.md)）

| 用例 | 位置 |
|------|------|
| AI 开启 → 离线任务回 `pending`（自动改派） | `test_offline_requeues_as_pending_when_ai_dispatch_enabled` |
| AI 关闭 → 离线任务回 `requeue_pending`（等确认） | `test_offline_requeues_as_pending_when_ai_dispatch_enabled` 的对照用例 |
| **`requeue_pending` 不被调度器派发**（守卫） | `test_requeue_pending_task_is_not_dispatched` |
| 确认后回 `pending` 可被派发 | `test_confirm_redispatch_moves_task_back_to_queue` |
| 非该状态确认 → 409 且状态不变（幂等保护） | `test_confirm_redispatch_rejects_wrong_status` |
| 待确认任务可取消 / 可指定节点改派 | `test_requeue_pending_task_can_be_cancelled`、`test_requeue_pending_task_can_set_dispatch_preference` |
| 前端「确认改派」按钮与失败重试 | `TaskDetailDrawerRequeue.test.tsx`（4 例） |

## 7. 迁移门禁与验证记录（2026-09-28）

| 项 | 结果 |
|----|------|
| 节点全量 `pytest tests/` | **182 passed** |
| 节点 `ruff check app tests` / `ruff format --check` | 全过 |
| 中枢 `ruff check app tests` | 仅 3 条**既有**告警（`resource_sync_service.py` B007/SIM117、`terminal_service.py` SIM105），与本次改动无关 |
| 中枢 `pytest tests/services tests/api`（438 passed / 6 failed / 8 errors） | 6 个 failed 中 5 个（`test_skill_registry` 4 例 + `test_dashboard::test_metrics_aggregates_eight_fields`）经 `git stash` 在**未改动代码**上复现，属既有失败；1 个（`test_task_lifecycle_accepted_and_completed`）单独运行通过，判为共享库状态导致的偶发；8 个 errors 为 `test_logs.py` 读取日志文件的 `PermissionError`（环境沙箱限制） |
| 前端 `tsc --noEmit` | 通过 |
| 前端 `vitest run` | 56 passed / 5 failed；5 个 failed 全部经 `git stash` 在**未改动代码**上复现（`TaskDetailDrawer` 1 例、`TaskNewInputBar` 1 例、`TaskNew` 3 例），属既有失败；本次新增/改动的 `TaskTable.test.tsx` 8 例全通过 |
| 前端 `vite build` | 通过（`✓ built in 16.20s`，仅有既有的 chunk >500kB 提示） |
| 迁移 `downgrade` 可回滚性 | 已编写 `downgrade`（drop 4 列 + FK + 索引）；**未在真实库执行验证** |
| 迁移图 | `alembic heads` 单头 `20260928_sched_resilience`，`down_revision=20260927_llm_params_window`；**未执行 `upgrade`**（需上线前由运维执行） |
| 二次走查（补修） | `set_dispatch_preference` 对 `failed` 误抛 409 的缺陷已修 + 7 个用例通过 |

## 8. 已知遗留（评审时可见，非本次阻塞项）

| 项 | 说明 | 影响 |
|----|------|------|
| 回避记录不随任务终态清理 | `dispatch_backoff._rejections` 仅在**改派成功**或**卡死兜底**时调 `clear_rejections`；任务正常 `completed`/`failed` 时该 task_id 的条目要等到下次 `excluded_nodes()` 调用的 TTL 清理才释放 | 进程内字典长期运行会有少量滞留键（单进程、条目极小）；不修复不影响正确性，属卫生问题 |
| `eligibility_gap` 只报首要缺口 | 同时内存与负载都不达标时只返回内存那一条 | 用户需解决一项后刷新才能看到第二项；如需并列展示需改返回结构（UT-04 因此未实现） |
| 中枢 `load_score` 缺失无 CPU 兜底 | 仅节点侧 `_resource_busy` 有 CPU 兜底；中枢对 metrics 缺失走百分比预筛 | 新节点始终上报两个指标，实际不受影响（UT-08 因此未实现） |
