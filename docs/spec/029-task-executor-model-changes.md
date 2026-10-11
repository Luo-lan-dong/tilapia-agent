# 任务执行模型选择与凭据下发 Changes 变更清单

关联决策：[ADR-029](../adr/029-task-executor-model-dispatch.md) / [Spec](./029-task-executor-model-spec.md)

> 按规则：每修改/新增/删除一个文件立即登记本清单（修/增/减 + 函数级明细），禁止开发完成后一次性补记。

## 变更清单

| # | 文件 | 类型 | 函数/类级明细 | 作用说明 | 日期 |
|---|------|------|--------------|---------|------|
| 1 | docs/adr/029-task-executor-model-dispatch.md | 增 | — | 决策：执行模型选择 + 凭据中枢下发（ADR-029） | 2026-09-10 |
| 2 | docs/spec/029-task-executor-model-spec.md | 增 | — | 功能规格：用户故事/流程/降级/验收 | 2026-09-10 |
| 3 | docs/spec/029-task-executor-model-harness.md | 增 | — | 测试清单与 CI 门禁 | 2026-09-10 |
| 4 | docs/spec/029-task-executor-model-changes.md | 增 | — | 本清单骨架 | 2026-09-10 |
| 5 | opc-central-dispatch-hub/app/models/task.py | 修 | Task +executor_model JSONB 列 | 执行模型选择落库（凭据不落库） | 2026-09-10 |
| 6 | opc-central-dispatch-hub/alembic/versions/20260910_task_executor_model.py | 增 | upgrade/downgrade | tasks.executor_model 迁移（已在开发库执行至 head） | 2026-09-10 |
| 7 | opc-central-dispatch-hub/app/schemas/task.py | 修 | +ExecutorModelSelection/normalize_executor_model/OPENCODE_FREE_DEFAULT_MODEL；TaskCreateRequest/TaskResponse +executor_model | 创建请求校验（extra=forbid + Literal source）与归一化 | 2026-09-10 |
| 8 | opc-central-dispatch-hub/app/services/task_service.py | 修 | create_task +chain_fields["executor_model"] 归一化落库 | 两条创建链路共用执行模型选择 | 2026-09-10 |
| 9 | opc-central-dispatch-hub/app/services/scheduler.py | 修 | +_build_executor_model_entry；dispatch_pending_tasks 组装 payload 时注入 | 派发 payload 实时读取中枢 LLM 配置注入凭据（循环内缓存，缺配置降级） | 2026-09-10 |
| 10 | opc-central-dispatch-hub/app/services/task_service.py | 修 | dispatch_task 手动指派分支复用 _build_executor_model_entry | 手动派发同样注入执行模型凭据 | 2026-09-10 |
| 11 | opc-agent-programming/app/models/schemas.py | 修 | TaskDispatchPayload +executor_model | Agent 接收执行模型选择 | 2026-09-10 |
| 12 | opc-agent-programming/app/executor/tilacode_client.py | 修 | create_session +model 参数；+ensure_integration_key（幂等注入 connect/key） | tilacode 运行时凭据注入与 session 模型指定 | 2026-09-10 |
| 13 | opc-agent-programming/app/executor/task_bridge.py | 修 | +_resolve_session_model；_prepare_session 调用并传 model | hub 来源注入凭据、free/缺省回退免费模型 | 2026-09-10 |
| 14 | opc-web-console/src/types/task.ts | 修 | TaskCreateRequest +executor_model；+ExecutorModelSelection | 前端类型 | 2026-09-10 |
| 15 | opc-web-console/src/components/TaskExecutorModelSelect.tsx | 增 | TaskExecutorModelSelect（Select 双选项 + Tooltip） | 执行模型选择器（US-1/US-2，免费选项带警示文案） | 2026-09-10 |
| 16 | opc-web-console/src/components/TaskNewToolbar.tsx | 修 | +executorModel/onExecutorModelChange props；绑定栏渲染选择器 | 新建页工具栏接线 | 2026-09-10 |
| 17 | opc-web-console/src/pages/TaskNew.tsx | 修 | +executorModel 状态；handleCreate 传 executor_model（hub 缺省不传） | 创建请求携带执行模型选择 | 2026-09-10 |
| 18 | opc-agent-programming/tests/test_task_bridge.py | 修 | make_callbacks 补 3 个产物回调；FakeTilacodeClient.create_session +model、+ensure_integration_key；_no_sleep 让出事件循环；+_run_task_to_completion 与 4 个 ADR-029 桥接测试 | 修复 ADR-028 遗留 15 个失败测试 + I3-I5 测试 | 2026-09-10 |
| 19 | opc-central-dispatch-hub/tests/services/test_task_executor_model.py | 增 | U1-U7/I1/I2 共 10 个用例（含日志脱敏与缓存复用） | hub 侧测试补齐 | 2026-09-10 |
| 20 | opc-central-dispatch-hub/app/models/task.py | 修 | Task +executor_model_actual String(200) | 节点上报的实际执行模型落库（ADR-029 增强） | 2026-09-11 |
| 21 | opc-central-dispatch-hub/alembic/versions/20260911_task_executor_model_actual.py | 增 | upgrade/downgrade | executor_model_actual 迁移（已在开发库执行） | 2026-09-11 |
| 22 | opc-central-dispatch-hub/app/services/task_event_handler.py | 修 | _handle_task_started +executor_model_actual 落库（截断 200）与日志 | agent.task_started payload 承载实际模型 | 2026-09-11 |
| 23 | opc-central-dispatch-hub/app/schemas/task.py | 修 | TaskResponse +executor_model_actual | 任务详情接口返回实际执行模型 | 2026-09-11 |
| 24 | opc-agent-programming/app/client/hub_client.py | 修 | +_TASK_STARTED_EVENT；+report_task_started | Agent 上报任务开始及实际模型（复用 agent.task_started 事件，无协议新增） | 2026-09-11 |
| 25 | opc-agent-programming/app/executor/task_bridge.py | 修 | BridgeCallbacks +on_task_started；_resolve_session_model 返回 (model, 实际描述)；_prepare_session session 创建后上报 | hub→deepseek/...，free/缺省→opencode/mimo-v2.5-free，降级附原因 | 2026-09-11 |
| 26 | opc-agent-programming/app/main.py | 修 | callbacks 装配 +on_task_started=client.report_task_started | 回调接线 | 2026-09-11 |
| 27 | opc-agent-programming/app/executor/workspace.py | 修 | TILACODE_CONFIG_TEMPLATE 移除 model 行 | 消除 tilacode.jsonc 误导（用户误把配置默认值当实际执行模型） | 2026-09-11 |
| 28 | opc-web-console/src/types/task.ts | 修 | Task +executor_model_actual | 前端类型 | 2026-09-11 |
| 29 | opc-web-console/src/components/TaskDetailInfo.tsx | 修 | Descriptions +「实际执行模型」行（有值才显示） | 任务详情一眼验证真实执行模型 | 2026-09-11 |
| 30 | opc-agent-programming/tests/test_task_bridge.py、test_workspace.py | 修 | make_callbacks +on_task_started；I3-I5 断言上报值；workspace 用例改断言无 model 字段 | Agent 测试同步 | 2026-09-11 |
| 31 | opc-central-dispatch-hub/tests/services/test_task_event_handler.py | 修 | +3 个 task_started 用例（落库/旧节点无字段/超长截断） | hub 测试补齐 | 2026-09-11 |
| 32 | opc-agent-programming/app/executor/task_bridge.py | 修 | _resolve_session_model | 支持 source=custom（与 hub 一致注入凭据并指定模型），source=opencode_free/缺省仍回退免费模型，未知 source 记告警 | 2026-09-13 |
| 33 | opc-agent-programming/tests/test_task_bridge.py | 增 | test_execute_task_custom_model_injects_key_and_selects_model | custom 模型注入与模型指定断言 | 2026-09-13 |
| 34 | opc-web-console/src/types/task.ts | 修 | ExecutorModelSelection | 移除 `opencode_free` 来源类型与相关注释 | 2026-09-13 |
| 35 | opc-web-console/src/components/TaskExecutorModelSelect.tsx | 修 | SelectKey / toKey / options / onChange | 移除「官方免费（可能失败）」选项与 `free` 编码 | 2026-09-13 |
| 36 | opc-web-console/src/components/TaskNewToolbar.tsx | 修 | 注释 | 移除 opencode_free 相关注释 | 2026-09-13 |
| 37 | opc-web-console/src/pages/TaskNew.tsx | 修 | 注释 | 移除「免费通道」注释 | 2026-09-13 |
| 38 | opc-central-dispatch-hub/app/schemas/task.py | 修 | ExecutorModelSelection / normalize_executor_model | source Literal 仅保留 hub/custom；删除 OPENCODE_FREE_DEFAULT_MODEL 与 free 归一化分支 | 2026-09-13 |
| 39 | opc-central-dispatch-hub/app/services/scheduler.py | 修 | _build_executor_model_entry 文档 | 移除 opencode_free 说明 | 2026-09-13 |
| 40 | opc-central-dispatch-hub/tests/services/test_task_executor_model.py | 修/减 | 删除 opencode_free 归一化、创建、派发用例；调整 U 编号 | 与移除 free 模型选项保持一致 | 2026-09-13 |
| 41 | opc-agent-programming/app/executor/task_bridge.py | 修 | _resolve_session_model 文档与分支 | 删除显式 `source == "opencode_free"` 分支，归并到未知 source 回退逻辑 | 2026-09-13 |
| 42 | opc-agent-programming/app/models/schemas.py | 修 | TaskDispatchPayload.executor_model 注释 | source 枚举移除 opencode_free | 2026-09-13 |
| 43 | opc-agent-programming/tests/test_task_bridge.py | 减 | test_execute_task_free_model_skips_injection | 删除显式 opencode_free 测试，缺省回退用例已覆盖 | 2026-09-13 |
| 44 | opc-agent-programming/app/executor/task_bridge.py | 修 | _resolve_session_model | 凭据注入不再携带任务目录：tilacode 凭据为全局存储，而 integration 注册表按目录懒初始化，新任务目录首请求必触发 500（Key method not found），恢复注入走默认目录 | 2026-09-13 |
| 45 | opc-agent-programming/app/executor/tilacode_client.py | 修 | ensure_integration_key + 重试常量 | connect/key 对 500 按指数退避重试（4 次尝试，2/4/8s），吸收 tilacode location 初始化窗口 | 2026-09-13 |
| 46 | opc-agent-programming/tests/test_tilacode_client.py | 增 | test_ensure_integration_key_success / retries_on_500 / raises_after_retries_exhausted / does_not_retry_non_500 | 注入成功、500 重试恢复、重试耗尽抛出、非 500 不重试四个用例 | 2026-09-13 |
| 47 | opc-agent-programming/tests/test_task_bridge.py | 修 | FakeTilacodeClient +integration_key_kwargs；hub/custom 注入用例断言 kwargs 为空 | 防回归：注入不得携带任务目录 | 2026-09-13 |

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-09-10 | 初始创建（ADR-029 骨架） | AI助手 |
| 2026-09-10 | 登记 ADR-029 全量实施：hub 落库/派发凭据下发、Agent 注入与模型指定、前端选择器、测试补齐（agent 86 passed / hub 新增 10 passed） | AI助手 |

（验证记录 2026-09-10：agent pytest 86 passed；hub 目标套件（executor_model/tasks/dispatch/lifecycle/scheduler/llm_provider）66 passed + 1 个既有 WS 抖动失败（test_websocket_malformed_event_ignored，单跑通过，与本次改动无关）；hub 全量 257 passed / 2 failed（均为 WS 连接类既有抖动）；前端 lint/tsc/build 通过并 copy_web_dist 同步（新 hash index-DdvOSD_R.js）；API 实测 POST /tasks 带 executor_model=opencode_free 返回归一化 {"source":"opencode_free","model":"mimo-v2.5-free"}，DELETE 清理 204）

（验证记录 2026-09-11：agent pytest 86 passed；hub test_task_event_handler 21 passed；前端 lint/build 通过并 copy_web_dist 同步；#20-#31 为「实际执行模型可验证」增强——用户反馈无法验证真实执行模型（tilacode.jsonc 静态默认值误导），现 Agent 在 session 创建后经 agent.task_started 上报实际模型，任务详情展示，降级附原因）

（验证记录 2026-09-13：修复 Agent 端 source=custom 未生效导致回退免费模型的问题；agent pytest 91 passed；custom 模型用例 `test_execute_task_custom_model_injects_key_and_selects_model` 通过）

（验证记录 2026-09-13：移除「官方免费（可能失败）」选项及相关代码；前端类型/组件注释清理；hub schema 与测试清理；Agent 端 opencode_free 分支清理；agent/hub 目标测试通过）

（验证记录 2026-09-13：#44-#47 修复 connect/key 每次必 500 的问题——根因：并发目录隔离修复后注入请求携带任务目录，触发 tilacode 新 location 懒初始化竞态（integration 注册依赖 models.dev 数据，缓存 TTL 5 分钟，过期走网络），注册完成前 handler 查不到 deepseek 即 Effect.die→500；凭据实为全局存储（makeGlobalNode），注入无需目录。修复：注入恢复默认目录 + 500 指数退避重试（最多 4 次）；agent pytest 94 passed；tilacode 侧源码佐证：packages/core/src/integration.ts connection.key 的 Key method not found die 分支、packages/core/src/plugin/models-dev.ts 的 key method 注册、packages/server/src/location.ts 的 per-directory 懒加载）
