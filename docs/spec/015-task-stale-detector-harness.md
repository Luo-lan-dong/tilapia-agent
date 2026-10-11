# 任务卡死检测与自动失败兜底 Harness

## 1. 测试金字塔

```
        E2E (1)
          │
      集成测试 (2)
          │
    单元测试 (3)
```

## 2. 单元测试（`tests/services/test_stale_task_detector.py`）

| 用例 | 场景 | 期望 |
|---|---|---|
| `test_fail_stale_tasks_marks_long_running_as_failed` | 制造 `running` 且 `updated_at = NOW - 31 min` 的任务 | 任务变 `failed`，`change_summary` 含「自动失败」字样，节点变 `idle` 且 `load - 1` |
| `test_fail_stale_tasks_ignores_fresh_running` | 制造 `running` 且 `updated_at = NOW - 5 min` 的任务 | 状态保持 `running`，`change_summary` 不写入 |
| `test_fail_stale_tasks_skips_locked_rows` | 同一任务被并发 `task_progress` 加锁 | 兜底扫描使用 `SKIP LOCKED` 不阻塞，状态机二次校验失败时安全跳过 |
| `test_fail_stale_tasks_does_not_overwrite_change_summary` | 任务 `change_summary` 已有非空内容 | 兜底**不**覆写 |
| `test_fail_stale_tasks_handles_missing_node` | 任务 `assigned_node_id` 指向不存在的节点 | 任务仍标 `failed`，记 `node_already_offline` WARNING |
| `test_threshold_loaded_from_settings` | Pydantic Settings 注入 `STALE_TASK_TIMEOUT_SECONDS=120` | 启动日志中 `threshold_seconds=120`；仅 `updated_at < NOW - 120s` 的任务被兜底 |
| `test_threshold_min_60_validation` | `STALE_TASK_TIMEOUT_SECONDS=30` 启动 | Pydantic 校验失败，应用启动失败 |

## 3. 集成测试（`tests/services/test_scheduler_with_stale_detector.py`）

| 用例 | 场景 | 期望 |
|---|---|---|
| `test_scheduler_runs_dispatch_then_stale_detector` | 启动 `run_scheduler(stop_event)`，制造 1 个 stale 任务 | 在 ≤1 个轮询周期（10s）后任务变 `failed`；调用 `_dispatch_once` 仍正常 |
| `test_scheduler_survives_stale_detector_exception` | 注入 mock 让 `_fail_stale_tasks` 抛 `OperationalError` | 异常被 catch 且记 `stale_task_detector_failed`，下一个 10s 周期继续执行 |
| `test_scheduler_stops_cleanly` | 调用 `stop_scheduler` | `stale_task_detector_started` 启动与 `task_scheduler_stopped` 停止日志均出现 |

## 4. 契约测试（`tests/api/v1/test_task_response.py`）

| 用例 | 场景 | 期望 |
|---|---|---|
| `test_task_response_includes_change_summary_after_stale_failure` | 兜底后调 `GET /v1/tasks/{id}` | 响应 `data.change_summary` 含自动失败原因 |

## 5. E2E 场景（手动验证，参见 [015-task-stale-detector-spec.md §10](./015-task-stale-detector-spec.md)）

| 场景 | 步骤 | 期望 |
|---|---|---|
| 制造僵尸任务并自动恢复 | `psql` 制造 1 个 `updated_at` 31 min 前的 `running` 任务；等待 10s | 任务变 `failed`；节点 `idle`；前端 `/tasks` 不再显示「执行中」；`/tasks/{id}` 的「方案」Tab 显示自动失败原因 |

## 6. 一次性脚本回归

- `scripts/fail_stuck_tasks_20260825.py` 已手动执行成功（清理 ac74cb06、d1f16732 两条历史数据），日志中无异常。该脚本仅做一次清理，不进入产品功能。

## 7. CI 门禁

- 后端 CI 必跑 `tests/services/test_stale_task_detector.py` 与 `tests/services/test_scheduler_with_stale_detector.py`。
- 覆盖率门槛：`scheduler.py` 提升至 ≥ 85%。
- Lint：`ruff check app/services/scheduler.py` 通过。

## 8. 回归风险

- 长任务（>30 分钟）：临时将 `STALE_TASK_TIMEOUT_SECONDS` 调高（如 7200），任务完成后调回。
- 高并发派发：`_dispatch_once` 与 `_fail_stale_tasks` 串行执行，单次总耗时 < 50ms，不会影响调度节拍（10s 间隔）。
- 数据库主库压力：仅查询 `running` 状态行，加 `idx_tasks_status_updated_at`（可选，本变更不强制）。

## 9. 验证清单

- [ ] 单元测试 7 项全通过
- [ ] 集成测试 3 项全通过
- [ ] 契约测试 1 项全通过
- [ ] E2E 手动场景通过
- [ ] 启动日志含 `stale_task_detector_started`
- [ ] 启动日志含 `threshold_seconds` 字段
- [ ] `ruff check` 无新增错误
- [ ] `coverage report` `scheduler.py` ≥ 85%
