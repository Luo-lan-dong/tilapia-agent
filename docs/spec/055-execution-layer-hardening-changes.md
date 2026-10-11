# 编程执行层缺陷全量修复与进度可视化 Changes 变更清单（P0–P2）

> 关联：[ADR-055](../adr/055-execution-layer-hardening.md) · [Spec](./055-execution-layer-hardening-spec.md) · [Harness](./055-execution-layer-hardening-harness.md)
> 规则：边开发边实时登记（修/增/减 + 函数级明细 + 作用说明），**禁止开发完成后一次性补记**。
> 范围约束：覆盖 P0-1~P0-5、P1-6~P1-9、P2-11~P2-13 与 P2-12；**不含** P2-10（节点侧 MCP，
> 用户决策 2026-09-27 不做）、**不含** tilacode 源码改造（含移植 `task`/LSP 工具）、
> **不含**存量任务 `progress_stage`/`progress_message` 数据回填。

## 计划变更点（编码前骨架）

### A. Agent 节点（`opc-agent-programming`）

| # | 文件 | 计划变更类型 | 计划函数/类级明细 | 作用说明 | 缺陷 |
|---|------|------------|-----------------|---------|------|
| A1 | `app/config.py` | 增 | `task_no_progress_timeout_seconds`（1800, ge=60）、`agent_progress_heartbeat_seconds`（20, ge=5）、`tilacode_config_dir` | 看门狗阈值与进度心跳、用户级配置目录定位 | P0-2/P2-13/D3 |
| A2 | `app/config.py` | 修 | `task_execution_timeout_seconds` 默认 300→0（ge=0）并改注释语义为「总时长上限，0=不限」 | 消除死配置；不误杀真实开发任务 | P0-2 |
| A3 | `app/executor/file_scanner.py` | 修 | `scan_file_tree` 改 `os.scandir` 增量遍历 + 逐目录 deadline；`EXCLUDED_NAMES` 增 `.env`/`.env.*`(保留 example)/`.mcp.json`/`.npmrc`/`id_rsa*` | 扫不死事件循环；凭据不进产物 | P0-1/P2-12 |
| A4 | `app/executor/workspace.py` | 增 | `WorkspacePathError`、`_resolve_within_root`、`_atomic_write_json` | 边界校验 + 原子写基础设施 | P0-4/P2-12 |
| A5 | `app/executor/workspace.py` | 修 | `prepare` 强制 `is_relative_to(workspace_root)`、兜底路径净化；`ensure_tilacode_config`/`upsert_provider_config`/`_upsert_instructions` 改原子写 + `deepcopy` | 越界拒绝；消除 check-then-write 竞态与模板污染 | P0-4/P2-12 |
| A6 | `app/executor/workspace.py` | 修 | `upsert_provider_config` 落点改用户级 `tilacode_config_dir` + `provider` 命名空间隔离 + TTL 清理 | API Key 不落任务工作区 | P0-4/D3 |
| A7 | `app/executor/event_translator.py` | 修 | `_tool_name` 取 `data.tool`；新增 `_stage_for_tool`/`_message_for_tool`（脱敏+截断）；`translate` 增 `tool.progress`/`retried`/`compaction.started|ended`；`ProgressUpdate` 增 `stage`/`detail` | 真实语义 + 信息量 | P1-8/P1-9 |
| A8 | `app/executor/sse_consumer.py` | 修 | 增 `last_activity_at`；`_consume_one_pass` 不再用 `wait_for` 拆流，改 `_await_idle` 轮询 `is_session_active`（1s×5） | 完成判定不撕流 | P1-6 |
| A9 | `app/executor/task_bridge.py` | 增 | `_task_watchdog`、`_project_lock`、`_progress_heartbeat` | 卡死检测、串行化、长任务心跳 | P0-2/P2-12/P2-13 |
| A10 | `app/executor/task_bridge.py` | 修 | `_pack_workspace`/`_report_file_tree_once` 走 `asyncio.to_thread` + 上限保护；`execute_task` 捕获集扩到 `WorkspacePathError`/`ValueError`；`finally` 初始化 `session_id`；`_extract_handoff_bundle` 防护 | 不阻塞 + 必有回执 + 安全解压 | P0-1/P0-3/P2-12 |
| A11 | `app/executor/task_bridge.py` | 修 | `_extract_last_assistant_text` 改 `/context`（回退 `/history`）；`_build_prompt` 去掉重复内联文档 | 修改说明回传；控制上下文膨胀 | P0-5/P1-7 |
| A12 | `app/executor/tilacode_manager.py` | 修 | stderr 消费任务；`monitor` 全异常防护 + 退避 + 次数上限 + unavailable 回调；健康检查循环；动态端口/密码暴露 | 自愈完整 | P2-11 |
| A13 | `app/executor/session_store.py` | 修 | `update_status` 单次 `_load`；`_load` 损坏时备份 + 告警 + 抛 `SessionStoreError` | 防全表被空表覆盖 | P2-12 |
| A14 | `app/client/hub_client.py` | 修 | `_run_task` 兜底 `except BaseException`（除 Cancelled）；`_send_task_progress` 透传 `stage`/`message`/`tool`/`detail` | 终点不丢回执与信息 | P0-3/P1-9 |
| A15 | `app/models/schemas.py` | 增 | `TaskDispatchPayload.timeout_seconds` | 对齐 agent-node-standards §5.3 | P0-2 |
| A16 | `app/main.py` | 修 | tilacode 可用性联动 `bridge_ready` 复位 + 心跳状态 | 重启期不误派发 | P2-11 |
| A17 | `.env.example` / `README.md` | 修 | 新增/修正配置项说明 | 配置可发现 | A1/A2 |

### B. 中枢（`opc-central-dispatch-hub`）

| # | 文件 | 计划变更类型 | 计划函数/类级明细 | 作用说明 | 缺陷 |
|---|------|------------|-----------------|---------|------|
| B1 | `app/models/task.py` | 增 | `progress_stage: String(32)`、`progress_message: String(500)` | 持久化当前动作 | P1-9 |
| B2 | `alembic/versions/20260927_task_progress_detail.py` | 增 | `upgrade`/`downgrade` | 迁移（含回滚） | P1-9 |
| B3 | `app/schemas/task.py` | 修 | `TaskResponse` 增两字段 | API 暴露 | P1-9 |
| B4 | `app/schemas/ws_messages.py` | 增 | `AgentTaskProgressPayload`（stage/message/tool/detail/todos 校验） | WS 消息必须有 Schema | P1-9 |
| B5 | `app/services/task_event_handler.py` | 修 | `_handle_task_progress` 校验并落库新字段 + 真实 stage 日志 | 落库与可观测 | P1-9 |

### C. 前端（`opc-web-console`）

| # | 文件 | 计划变更类型 | 计划函数/类级明细 | 作用说明 | 缺陷 |
|---|------|------------|-----------------|---------|------|
| C1 | `src/types/task.ts` | 修 | `Task` 增 `progress_stage?`/`progress_message?`/`progress_detail?` | 类型对齐 | P1-9 |
| C2 | `src/components/TaskSummary.tsx` | 修 | 增「当前动作」区（`STAGE_LABELS` 映射）+ 空态改造 | 摘要可见 | P1-9/P2-13 |
| C3 | `src/components/TaskDetailInfo.tsx` | 修 | 增「当前动作」项 | 详情一致 | P1-9 |
| C4 | `src/pages/TaskNew.css` | 增 | `.task-summary__action*` 样式 | 展示样式 | P1-9 |
| C5 | `src/__tests__/components/TaskSummary.test.tsx` | 增 | UT-47~UT-50 | 三态回归 | P1-9 |

### D. 文档与规范

| # | 文件 | 计划变更类型 | 明细 | 作用说明 |
|---|------|------------|------|---------|
| D1 | `docs/adr/055-execution-layer-hardening.md` | 增 | ADR 全文 | 决策记录 |
| D2 | `docs/spec/055-execution-layer-hardening-spec.md` | 增 | Spec 全文 | 需求/流程/验收 |
| D3 | `docs/spec/055-execution-layer-hardening-harness.md` | 增 | Harness 全文 | 测试金字塔与门禁 |
| D4 | `docs/spec/055-execution-layer-hardening-changes.md` | 增 | 本文件 | 变更清单 |
| D5 | `docs/INDEX.md` | 修 | 1.1 / 1.2 / 1.3 / 六 四处登记 | 索引 |
| D6 | `docs/standards/api-standards.md` | 修 | §4.2 payload 扩展 + 变更记录 | 协议规范 |
| D7 | `docs/standards/agent-node-standards.md` | 修 | §5.3 超时语义、§6 进度字段 | 节点规范 |

### E. 测试

| # | 文件 | 计划变更类型 | 明细 | 作用说明 |
|---|------|------------|------|---------|
| E1 | `opc-agent-programming/tests/test_file_scanner_rules.py` | 增 | UT-01~UT-04 | 扫描/排除/上限 |
| E2 | `opc-agent-programming/tests/test_workspace.py` | 增 | UT-05~UT-10 | 边界/原子写/凭据 |
| E3 | `opc-agent-programming/tests/test_session_store.py` | 增 | UT-11~UT-12 | 存储完整性 |
| E4 | `opc-agent-programming/tests/test_task_bridge.py` | 增 | UT-13~UT-26、IT-1~IT-6 | 回执/看门狗/完成判定/锁/凭据 |
| E5 | `opc-agent-programming/tests/test_event_translator.py` | 增 | UT-27~UT-33 | 新 schema 映射 |
| E6 | `opc-agent-programming/tests/test_tilacode_manager.py` | 增 | UT-34~UT-39 | 子进程自愈 |
| E7 | `opc-agent-programming/tests/test_config.py` | 修 | 新配置项默认值与约束 | 配置回归 |
| E8 | `opc-central-dispatch-hub/tests/services/test_task_event_handler.py` | 增 | UT-40~UT-43 | 中枢落库 |
| E9 | `opc-central-dispatch-hub/tests/api/v1/test_task_lifecycle.py` | 增 | UT-44~UT-46 | Schema/迁移/响应 |
| E10 | `opc-web-console/src/__tests__/components/TaskSummary.test.tsx` | 增 | UT-47~UT-50 | 前端三态 |

## 变更清单（实际实施明细）

> 边开发边登记；每条完成后立即追加。

### 阶段 S1–S4（节点侧，已完成并通过 143 项节点测试）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/executor/file_scanner.py` | 修 | `scan_file_tree`：`Path.rglob`+`sorted` → `os.scandir` 增量遍历（逐目录检查 deadline、到期即返回） | 扫描不再在截止检查前物化整棵树（P0-1） |
| `app/executor/file_scanner.py` | 增 | `_is_sensitive_name`、`_should_descend`、`is_excluded_relative_path`；`SENSITIVE_NAMES`（`.mcp.json`/`.npmrc`/`id_rsa*` 等）、`SCAN_DEADLINE_SECONDS` | 凭据文件不进产物清单；`.tilacode` 不可整棵剪枝（保留 `rules/**` 例外）（P2-12） |
| `app/executor/workspace.py` | 增 | `WorkspacePathError`、`_atomic_write_text`、`_read_json_object`、`_strip_credentials`、`sanitize_provider_name`、`provider_namespace`、`default_tilacode_config_dir` | 边界校验 + 原子写 + 用户级凭据命名空间基础设施（P0-4/P2-12/D3） |
| `app/executor/workspace.py` | 修 | `prepare` 强制 `is_relative_to(workspace_root)` + `_safe_task_segment` 净化兜底 task_id | 拒绝 `/etc`、`../..` 越界（P0-4） |
| `app/executor/workspace.py` | 修 | `ensure_tilacode_config` 改原子写并**剥离历史遗留凭据段**；`upsert_provider_config` 落点改用户级配置 + 命名空间 + `_hub_managed_providers` 记账；新增 `cleanup_provider_configs`（TTL 7 天） | apiKey 不落任务工作区；同节点多任务凭据互不覆盖（P0-4/D3） |
| `app/executor/workspace.py` | 修 | 模块级 `_write_specification_docs`/`_read_plan_documents`/`_materialize_rule_docs`/`_upsert_instructions`/`_ensure_index_skeleton`（`deepcopy` 模板 + 原子写），`WorkspaceManager` 保留同名方法委托 | 消除模板污染与读-改-写竞态；plan 文档读取失败降级不抛错（P2-12/P1-7） |
| `app/executor/session_store.py` | 修 | `update_status` 改**单次** `_load`；`_load(strict=)` + `_handle_corrupt` + `_backup_corrupt_file`（损坏文件备份为 `.corrupt-<ts>`，写路径 strict 抛错） | 防「读到空表 → 写入单条 → 覆盖全量」（P2-12） |
| `app/executor/session_store.py` | 修 | `TaskMappingStore._load/_handle_corrupt` 同上 | 映射登记簿同等级保护（P2-12） |
| `app/executor/event_translator.py` | 修 | `_tool_name` → `_tool_name_of`（读真实 `data.tool` 字符串）；新增 `_tool_activity_message`/`_primary_target`（绝对路径缩为末两段、命令摘要 ≤60）、`_todos_message`、`_step_detail`（tokens/cost/files）；`translate` 新增 `tool.progress`/`retried`/`compaction.started|ended` 分支与 stage 状态机 | 工具名不再恒为 `unknown`；阶段/动作/明细齐备（P1-8/P1-9） |
| `app/executor/event_translator.py` | 增 | `STAGE_*` 十项阶段常量、`STAGES`、`_TOOL_STAGE`、`MESSAGE_MAX_CHARS`；`ProgressUpdate` 增 `detail`；`EventTranslator` 增 `stage`/`message`/`tool_name` 属性 | 阶段封闭枚举与心跳复用（D2/P2-13） |
| `app/executor/sse_consumer.py` | 修 | 新增 `_IdleReached`、`_IDLE_POLL_INTERVAL_SECONDS`/`_IDLE_POLL_TOTAL_SECONDS`；`run()` 重写：静默到期或流结束且处于静默态 → `_await_until_idle_or_active()`（1s×5 轮询 active）判完成；仅真实断流才退避重连 | 完成判定不再靠超时拆流，任务关键路径少 ~25s（P1-6） |
| `app/executor/sse_consumer.py` | 增 | `_last_activity_at` 与 `last_activity_at`/`has_result`/`result`/`translator` 属性；`_consume_one_pass` 每个事件刷新活动时间 | 无进度看门狗与心跳的数据源（P0-2/P2-13） |
| `app/executor/sse_consumer.py` | 修 | 进度回调签名改为 `Callable[[ProgressUpdate], Awaitable[None]]` | 阶段/动作/明细可透传（P1-9） |
| `app/executor/task_bridge.py` | 增 | `_task_watchdog`（无进度 + 可选总时长双判定 → `task_timeout` 日志 + `interrupt` + `fail`）、`_progress_heartbeat`（静默期补发「仍在执行」）、`_ensure_periodic_pace`（周期任务真实耗时自我节流）、`_cancel_previous_consumer` + `_consume_tasks`（轮次切换结束上一轮遗留消费，防僵尸订阅） | 卡死自愈、前端不假死、轮次间不泄漏 SSE 连接（P0-2/P2-13） |
| `app/executor/task_bridge.py` | 修 | `execute_task`：移除硬编码静默窗口监控，改为挂看门狗 + 心跳任务；捕获集新增 `WorkspacePathError`；`finally` 初始化 `session_id` 并清理心跳/看门狗/消费任务 | 必有回执；`UnboundLocalError` 隐患消除（P0-3） |
| `app/executor/task_bridge.py` | 修 | `_report_file_tree_once` 经 `asyncio.to_thread`；`materialize_rule_docs`/`_persist_attachments` 亦经线程池 | 重 IO 不阻塞事件循环（P0-1） |
| `app/executor/task_bridge.py` | 修 | `_resolve_session_model` 改用户级配置 + 命名空间（去掉 `directory` 参数）；`_run_session` 用 `_forward_progress` 透传 `stage/message/tool/detail` 并记录上报时间 | 凭据移出工作区 + 进度字段真正上报（P0-4/P1-9） |
| `app/executor/task_bridge.py` | 增 | `_HANDOFF_MAX_ENTRIES`/`_HANDOFF_MAX_MEMBER_BYTES`/`_WATCHDOG_POLL_INTERVAL_SECONDS`/`_PERIODIC_MIN_SLEEP_SECONDS` 常量 | 安全解压与周期节流参数 |
| `app/config.py` | 修 | `task_execution_timeout_seconds` 默认 300→0（`ge=0`）并改注释语义为可选总上限 | 消除死配置且不误杀真实任务（P0-2/D1） |
| `app/config.py` | 增 | `task_no_progress_timeout_seconds`（1800, ge=60）、`agent_progress_heartbeat_seconds`（20, ge=5）、`tilacode_config_dir` | 看门狗阈值、心跳间隔、用户级配置目录（D1/D3/P2-13） |
| `app/models/schemas.py` | 增 | `TaskDispatchPayload.timeout_seconds`（`ge=0`） | 对齐 agent-node-standards §5.3 任务级超时（P0-2） |
| `app/client/hub_client.py` | 修 | `_run_task` 兜底 `except BaseException`（`CancelledError` 原样上抛）→ `report_failed("agent_internal_error: ...")` | 未预期异常也必有终态回执（P0-3） |
| `app/client/hub_client.py` | 修 | `report_progress`/`_send_task_progress` 增 `stage`/`message`（截断 120）/`tool`/`detail`；新增 `_PROGRESS_MESSAGE_MAX_CHARS` | 进度信息真正到达中枢（P1-9） |
| `app/executor/__init__.py` | 修 | 模块 docstring 说明「重 IO 必须经 `asyncio.to_thread`」 | 约定显式化 |
| `tests/test_event_translator.py` | 修/增 | 全部既有用例改真实 schema（`data.tool`）；新增 `plan 阶段映射`、`相对路径 message`、`绝对路径缩短`、`tool.progress`、`compaction 映射与恢复`、`retried`、`step.ended detail`、`todo → in_progress 动作` | UT-27~UT-33 覆盖（P1-8/P1-9） |
| `tests/test_task_bridge.py` | 修 | `on_progress` 回调签名与断言适配新字段；`_no_sleep` 改 1ms 真实等待（防周期任务饿死消费）；`FakeTilacodeClient` 订阅计数改为按会话；`workspace` fixture 增隔离 `config_dir`；自定义模型用例断言改「用户级配置 + 命名空间 + 工作区无凭据」；idle/总结轮流序列与订阅次数适配新完成判定 | 测试对齐 ADR-055 行为（P0-4/P1-6/P1-9） |

**验证（S1–S4）**：`python -m pytest tests/` → **143 passed**；`ruff check app tests` 仅剩 2 项**存量**问题
（`tests/test_tilacode_client.py` E501，非本次改动文件）；`ruff format --check` 对本次改动文件已格式化。

### 阶段 S5（子进程自愈与配置，已完成）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/executor/tilacode_manager.py` | 修 | 模块 docstring 列明 P2-11 修复项；新增常量 `_RESTART_BACKOFF_INITIAL_SECONDS`(1s)/`_RESTART_BACKOFF_MAX_SECONDS`(60s)/`_MAX_RESTART_ATTEMPTS`(5)/`_HEALTH_FAILURE_RESTART_THRESHOLD`(3)/`_STDERR_LINE_MAX_CHARS` | 退避与阈值参数化 |
| `app/executor/tilacode_manager.py` | 增 | `_drain_stderr` + `self._stderr_task` | stderr 被持续消费，子进程不再因管道写满而阻塞（P2-11①） |
| `app/executor/tilacode_manager.py` | 增 | `is_unavailable` / `auth()` 属性方法 | 不可用状态与动态凭据取值入口（P2-11③⑤） |
| `app/executor/tilacode_manager.py` | 修 | `monitor`：已退出进程不重复 `wait`、**摘掉死进程引用**、`_restart_with_backoff` 退避重启、回调异常不终止循环 | 修复「重启失败即无退避空转」+「单次回调异常永久失能」（P2-11②③） |
| `app/executor/tilacode_manager.py` | 增 | `_restart_with_backoff`、`_mark_unavailable`、`_notify_unexpected_exit` | 退避序列 1s→2s→4s…封顶 60s；超上限进入 unavailable（P2-11③） |
| `app/executor/tilacode_manager.py` | 增 | `check_health`、`health_monitor`（`_cancel_pipe_tasks` 取代 `_cancel_stdout_task`） | 落地周期性健康检查（连续 3 次失败主动重启），消费此前的死配置（P2-11⑥） |
| `app/executor/tilacode_client.py` | 修 | `base_url` 支持回调或字符串 + `base_url` 属性 / `set_base_url`；`AsyncClient` 不再固化 `base_url`，`_request`/`_stream_events` 改用完整 URL | 托管进程重启换端口后请求不再打到旧端口（P2-11④） |
| `app/executor/tilacode_client.py` | 增 | `get_context`（`GET /api/session/{id}/context`）；`get_history` docstring 澄清返回 durable 事件分页 | P0-5 正确取数端点 |
| `app/main.py` | 修 | `_build_task_bridge`：`base_url` 改回调（动态 `resolved_port`）+ `WorkspaceManager(config_dir=...)` | 动态端口 + 用户级凭据目录（P2-11④/D3） |
| `app/main.py` | 增 | `on_tilacode_exit` 回调；`on_session_ready` 检查 `is_unavailable`；挂载 `tmgr.health_monitor()` | 重启成功恢复 `bridge_ready`/idle，失败则清除并上报 unavailable（P2-11⑤） |
| `app/executor/task_bridge.py` | 增 | `_is_safe_archive_member` | handoff 包解压拒绝反斜杠与 Windows 盘符成员（P2-12⑥） |
| `app/executor/task_bridge.py` | 修 | `_extract_handoff_bundle`：条目数/单文件上限、安全成员过滤、`namelist()` 移入 `with` 块 | 防 zip 炸弹与块外引用（P2-12⑥） |
| `tests/test_tilacode_manager.py` | 增 | 9 项用例：stderr 消费、死进程引用清理 + 回调一次、退避翻倍 1s/2s/4s、超上限 unavailable、回调异常不传播、健康检查异常、健康检查阈值触发 kill、端口/密码动态、主动 stop 不误判异常 | UT-34~UT-39（P2-11） |

**验证（S5）**：`python -m pytest tests/` → **154 passed**（新增 9 项）；`ruff check app tests` 仍仅 2 项存量问题。

### 阶段 S6（中枢：加列 / 校验 / 迁移，已完成）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/models/task.py` | 增 | `progress_stage: String(32)`、`progress_message: String(500)`（均 nullable + 注释） | 持久化 Agent 当前动作（P1-9） |
| `alembic/versions/20260927_task_progress_detail.py` | 增 | `upgrade`（add_column ×2）/ `downgrade`（drop_column ×2）；`down_revision = 20260927_terminal` | 迁移含完整回滚（backend-component-standards §5） |
| `app/schemas/task.py` | 修 | `TaskResponse` 增 `progress_stage` / `progress_message` | API 暴露给前端（P1-9） |
| `app/schemas/ws_messages.py` | 增 | `TASK_PROGRESS_STAGES`（封闭枚举）、`TASK_PROGRESS_MESSAGE_MAX_CHARS`(500)、`AgentTaskProgressPayload`（stage/message/tool/detail/todos，全部可选） | WS 消息必须有 Schema（backend-component-standards §2.4） |
| `app/services/task_event_handler.py` | 修 | `_handle_task_progress` 落库 `progress_stage`/`progress_message`；日志 `stage` 改用**真实上报值**（原硬编码 `execution`） | 摘要面板有数据来源（P1-9） |
| `app/services/task_event_handler.py` | 增 | `_sanitize_progress_stage`（非枚举值告警忽略）、`_sanitize_progress_message`（类型校验 + 超长截断） | 宽松校验：单字段异常不丢弃整条进度（P1-9） |
| `tests/services/test_task_event_handler.py` | 增 | 5 项：stage/message 落库、未知 stage 忽略但进度更新、超长 message 截断、旧 payload 新列保持 NULL、Schema 校验（越界/缺 task_id 拒绝、新键全可选） | UT-40~UT-44（P1-9） |

**验证（S6）**：`tests/services/test_task_event_handler.py` → **29 passed**；`ruff check` 对改动文件 **All checks passed**；
迁移 `20260927_task_progress_detail` **已在库中应用**（`alembic history` 链：`..._terminal → ..._task_progress_detail → ..._llm_params_window`），
实测 `information_schema.columns` 确认两列存在且类型匹配（varchar(32) / varchar(500)、nullable）。

### 阶段 S7（前端：任务摘要当前动作，已完成）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `src/types/task.ts` | 修 | `Task` 增 `progress_stage?` / `progress_message?` | 类型对齐中枢响应（P1-9） |
| `src/components/TaskSummary.tsx` | 增 | `STAGE_LABELS`（十项阶段中文映射）、`ACTIVE_STATUSES`、`CurrentAction` 组件（`data-testid="task-summary-action"`） | 展示「当前动作」（阶段标签 + 动作描述）（P1-9/P2-13） |
| `src/components/TaskSummary.tsx` | 修 | 空态文案区分执行中/终态（`Agent 暂未上报待办明细` / `暂无待办明细`）；`STATUS_LABELS` 补 `review` | 无待办时不再只有一句空话（P1-9） |
| `src/components/TaskDetailInfo.tsx` | 增 | `STAGE_LABEL_MAP`、`currentActionText(task)` | 详情补「当前动作」行，未上报时展示 `—`（P1-9） |
| `src/pages/TaskNew.css` | 增 | `.task-summary__action*` 5 个类（`--primary-bg`/`--primary-border` 语义变量） | 亮/暗双主题一致（ADR-052 颜色收敛） |
| `src/__tests__/components/TaskSummary.test.tsx` | 增 | 5 项：有 todo + 有动作、无 todo + 有动作（不再只显示「暂无待办明细」）、无 todo 无动作（执行中/终态两种空态）、未知 stage 容错、task 为空不渲染 | UT-47~UT-50（P1-9） |

**验证（S7）**：新增用例 **5 passed**；`npm run typecheck`（tsc -b）通过；`npm run build` 通过（产物生成）；
全量前端回归 **53 passed / 4 failed**，其中 `TaskNew.test.tsx` 的 3 项经 `git stash` **基线复跑确认同样失败**（聊天流相关，与本次改动文件无关），另 1 项为既有失败，均非本次引入。

### 阶段 S8（回归与收尾）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/executor/workspace.py` | 增 | `WorkspaceManager.rule_docs_materialized` | 逐份比对目标文件字节 + AGENTS.md 存在性，判断规则文档是否可复用（P1-7） |
| `app/executor/task_bridge.py` | 修 | `execute_task` 中规则文档落盘前先 `rule_docs_materialized` 判定，命中则跳过并记 `rule_docs_reuse_existing` | 链上复用工作目录时不再重写规则文档/AGENTS.md，避免上下文 epoch 无谓增量（P1-7） |
| `tests/test_rule_docs.py` | 增 | 3 项：内容一致判定复用、内容变化判定需重写、目标文件缺失判定需重写 | P1-7 守卫用例 |

| 项目 | 结果 |
|------|------|
| 节点 `pytest tests/` | **157 passed** |
| 中枢 `pytest tests/services/test_task_event_handler.py` | **29 passed** |
| 前端 `vitest`（新增用例） | **5 passed** |
| 前端 `tsc -b` / `vite build` | 均通过 |
| `ruff check`（节点/中枢改动文件） | 节点仅剩 2 项**存量**问题；中枢 All checks passed |
| 迁移 | 链完整、已应用、列已实测存在（含 downgrade 定义） |

## 登记记录

- 2026-09-27：创建骨架（ADR-055 四件套建立；Spec/Harness 同步完成，未写生产代码）。
- 2026-09-27：范围经用户确认——**全链路修复**（节点 + 中枢 + 前端）、超时采用
  **无进度看门狗**语义、**文档先行**；P2-10（节点侧 MCP）明确不做；tilacode 源码不改造。
- 2026-09-27：S1–S4 节点侧实施完成并登记（143 passed）。
- 2026-09-27：S5 子进程自愈与配置实施完成（154 passed，新增 `tests/test_tilacode_manager.py` 9 项）。
- 2026-09-27：S6/S7 中枢与前端实施完成（中枢 29 passed、前端新增 5 passed、typecheck/build 通过）。
- 2026-09-27：**实施期间发现并修复的额外缺陷**（均登记在上表对应行）：
  1. `TilacodeManager.monitor` 在进程已退出时不清理 `self._process`，重启失败路径会绕过退避**空转**——
     已改为摘掉死进程引用并跳过重复 `wait`（S5，由新测试 `test_monitor_clears_dead_process_and_notifies_once` 守卫）；
  2. `TaskBridge` 轮次切换未结束上一轮 SSE 消费任务，遗留**僵尸订阅**（真实环境白占一条 SSE 连接）——
     新增 `_consume_tasks` + `_cancel_previous_consumer`（S3）；
  3. 周期任务（看门狗/心跳）在 `asyncio.sleep` 被替换为让出式实现时会把事件循环**饿死**——
     新增 `_ensure_periodic_pace` 按真实耗时自我节流（S2/S4，属防御性加固）。
- 2026-09-27：**未做**：存量任务的 `progress_stage`/`progress_message` 数据回填（NULL 即「无当前动作」，前端已兼容）；
  P2-10 节点侧 MCP；tilacode 源码改造（含 `task` 子代理工具移植）。
- 2026-09-27：**既有失败（非本次引入，已复跑确认）**：
  1. 前端 `src/__tests__/pages/TaskNew.test.tsx` 3 项聊天流用例失败——`git stash` 移除本次前端改动后**基线同样失败**
     （该文件不引用 `TaskSummary`，与本次改动文件无关）；
  2. 中枢 `tests/api/v1/test_task_lifecycle.py` 2 项失败——失败点是 `hub.task_dispatch` 的 task_id 与测试期望不符
     （`assert '7c7d0c25-…' == 'f13cc2fe-…'`），根因是**共享开发库存在残留 pending 任务**导致调度器派发了别的任务；
     该文件在本次改动前即失败（stash 尝试因新增迁移文件未纳入版本控制而失败，但失败点与本次新增字段无任何关联）。
     建议后续为这两个文件补测试隔离（清理 pending 任务或按 task_id 过滤断言）。
- 2026-09-27：**部署提示**：迁移 `20260927_task_progress_detail` 已在当前开发库应用（`alembic current` 为
  `20260927_llm_params_window`，链中包含本迁移）；其它环境升级时执行 `alembic upgrade head` 即可，
  前端与节点无需额外配置变更。

## 补丁：任务永不执行的必现回归修复（2026-09-27，发现于本 ADR 实施后）

### 现象

中枢持续给节点派发任务，**执行层永远不执行**：任务停在「执行中」，节点侧无实际动作；
用户尝试多次修复无效。

### 根因链（两个叠加缺陷）

1. **原 D3「凭据移出工作区」策略与 tilacode 加载机制冲突**：
   - 用户级 `~/.config/tilacode/tilacode.jsonc` **只在 server 启动时快照一次**，
     运行期不热重载 → 任务专属 provider 写用户级后运行中的 server 看不到
     （`ModelUnavailableError`）；
   - 若把复数 `providers` 或记账键 `_hub_managed_providers` 写进用户级配置，
     新版 tilacode 严格 schema 校验会以「Unrecognized keys」**启动即退出** →
     节点重启循环；
   - 正确载体是**工作目录** `.tilacode/tilacode.jsonc` 顶层**单数** `provider` 键
     （项目级配置随会话动态加载）。

> **2026-09-28 实机实证补充（编译 Windows 二进制实测，见 ADR-055「tilacode 配置 schema 契约」）**：
> 复数 `providers` **在全局与工作区两处都会被严格校验拒绝**（`Unrecognized key: providers`），
> 不存在「工作区静默忽略」这种情形；该键只能由 V1 `provider` 经 `ConfigMigrateV1.migrate` 生成。
> 因此工作区配置必须**全 V1 键、零 V2 键**——加任一 V2 专有键（`providers`/`permissions`/
> `agents`/`plugins`/`commands`/`references`…）都会让**整个文件作废**、任务不执行。
> 另实测确认 V1 provider 的正确键位：`api` 为**字符串 URL**，密钥在 **`options.apiKey`**
> （放顶层会被忽略）。
2. **`ensure_tilacode_config` 的「顺手剥离凭据」造成必现回归**（本次补丁修复的错误）：
   - `_strip_credentials` 会 `pop("provider")` / `pop("providers")`；
   - 执行顺序为 `prepare()` → **`ensure_tilacode_config()`** → `_resolve_session_model()`，
     即**每次任务开始时先删 provider 段**；
   - 首轮任务因随后重新写入 provider 而侥幸可用；**复用工作目录的后续任务**
     在该删除后若未成功重写（或 create_session 前的任何一步失败），模型解析即失败；
   - 实际等价于「provider 段被反复删除」，与用户新方案（provider 必须在工作目录）**直接冲突**。

### 修复内容

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/executor/workspace.py` | 删 | `_strip_credentials`（含调用点） | 删除「每次任务开始剥离 provider」这一必现回归源头 |
| `app/executor/workspace.py` | 增 | `_heal_workspace_config(config) -> bool` | 剔除毒键 `_hub_managed_providers`；复数 `providers` 归并为单数 `provider`（严格 schema 自愈） |
| `app/executor/workspace.py` | 修 | `ensure_tilacode_config`：只「文件不存在时写模板」+「存在时仅自愈」；**永不覆盖/删除 provider 段** | 保证任务专属 provider 在工作目录配置中持续存在 |
| `app/executor/workspace.py` | 删 | `cleanup_provider_configs` + `_MANAGED_PROVIDER_TTL_SECONDS` | 用户级写入已废弃，删除该方法以**彻底移除毒化用户级配置的代码路径** |
| `app/executor/workspace.py` | 修 | `_MANAGED_KEY` 注释改为「仅用于写入时自愈剔除的毒键」 | 语义澄清 |
| `tests/test_workspace.py` | 增 | 3 项：`test_ensure_tilacode_config_preserves_provider_section`（**守卫：provider 与 apiKey 必须存活**）、`test_ensure_tilacode_config_heals_poisoned_keys`、`test_ensure_tilacode_config_writes_template_when_missing` | 锁定回归，防再次「顺手清理凭据」 |

### 验证

- 节点 `pytest tests/` → **160 passed**（新增 3 项工作目录配置守卫用例）；
- 回归用例断言：写入 provider 后再次 `ensure_tilacode_config`，`provider.<ns>.options.apiKey`
  与 `permission`/`default_agent` 模板段**同时存在**；
- 本机用户级配置 `~/.config/tilacode/tilacode.jsonc` 实测**干净**（仅 `$schema`），无历史毒键残留。

### 升级提示（生产节点必读）

若节点曾运行过本 ADR 的早期版本（写用户级配置 + TTL 清理），请检查并清理用户级配置：

```bash
cat ~/.config/tilacode/tilacode.jsonc    # 应只含 $schema / 用户自己的配置
# 若出现 "providers" 或 "_hub_managed_providers" 顶层键，删除后重启节点服务
```

任务工作目录内的 `.tilacode/tilacode.jsonc` **无需手工处理**：新代码会在下次任务时自动自愈
（剔毒键、复数归并单数），并保留 provider 段。

---

## 事故事后复盘与二次修复（2026-09-27，现场日志实证）

### 现场证据（节点日志，用户提供）

```
[error] tilacode_stderr: Error: Configuration is invalid at /root/.config/tilacode/tilacode.jsonc
[error] tilacode_stderr: ↳ Unrecognized keys: providers, _hub_managed_providers
[warning] tilacode_health_check_error: 'All connection attempts failed' url=http://127.0.0.1:4096/api/health
```

### 两个阶段（同一毒键造成，表现不同）

| 阶段 | 时间 | 现象 | 机制 |
|------|------|------|------|
| A | 21:56–22:14 | 健康检查持续 `All connection attempts failed`、`tilacode_port_parse_failed`、`Task was destroyed but it is pending!` | **tilacode 启动即退出**（配置校验失败），节点反复重启，服务从未就绪 |
| B | 22:18 起 | `POST /api/session 200` → `prompt 200` → `sse_drain_finished` → **零 step 事件** → 每 40s `tilacode_sse_disconnected` → 1800s 后被看门狗判 `task_no_progress_timeout:1800` | 配置被判非法后**整个文件不参与 merge** → 任务专属 provider 从未进 catalog → `SessionRunnerModel.resolve()` 抛 `ModelUnavailableError` → drain 静默失败（**不发事件**） |

### 根因

本 ADR 早期实现把 `providers`（复数，V2 才有的键）与记账键 `_hub_managed_providers`
写进了**用户级**配置。tilacode 的 `isV1` 判定是「出现任一 V1 专属键即按 V1 解析」
（`packages/core/src/v1/config/migrate.ts:30-33`），而 V1 只有单数 `provider`，
因此在含 `permission` 等 V1 键的文件里，`providers` 属未知键 → 严格 schema 校验
判定配置非法。代码虽已停止写用户级配置，但**磁盘残留持续造成故障**。

### 二次修复内容

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/executor/workspace.py` | 增 | `_KNOWN_CONFIG_TOP_LEVEL_KEYS`（V1 ∪ V2 顶层键白名单）、`_V1_ONLY_TOP_LEVEL_KEYS`、`_looks_like_v1_config`、`WorkspaceManager.sanitize_user_config` | 启动自检：清理用户级配置中的未知顶层键；**V1 感知**（纯 V2 文件里的 `providers` 合法，不误删）；损坏文件重置为 `{}`；被删键名记 ERROR 日志（不打印值，避免凭据入日志） |
| `app/main.py` | 修 | 启动第 2 步：`WorkspaceManager(...).sanitize_user_config()` | 每次启动前清理一次残留，清理失败不阻断启动 |
| `app/executor/tilacode_manager.py` | 增 | `_CONFIG_ERROR_MARKERS`、`_is_config_error_line` | 识别 stderr 中的配置非法行 |
| `app/executor/tilacode_manager.py` | 修 | `_drain_stderr`：配置非法行以 **ERROR** 事件 `tilacode_config_invalid` 记录 | 该错误此前混在大量 WARNING 中被淹没，导致排查耗时数小时 |
| `app/executor/sse_consumer.py` | 增 | `check_startup_stall(threshold)`（`_admitted_at`/`_step_started` 状态） | admitted 后阈值内无 `step.started` 即判失败，原因含排查方向；**60s 内暴露**而非等 1800s |
| `app/executor/task_bridge.py` | 增 | 看门狗内调用 `check_startup_stall`；常量 `_STARTUP_STALL_TIMEOUT_SECONDS=60` | 同上 |
| `tests/test_workspace.py` | 增 | 5 项：毒键清理（含 V1 夹具）、纯 V2 `providers` 不误删、合法键保留、损坏文件重置、文件不存在不落盘 | 锁定自检行为 |
| `tests/test_tilacode_manager.py` | 增 | 2 项：`_is_config_error_line` 识别（含真实 ANSI stderr 样例）、`_drain_stderr` 配置行走 ERROR 事件 | 锁定告警升级 |
| `tests/test_sse_consumer.py` | 增 | 6 项启动停滞判定用例 | 锁定快速失败 |

### 验证

- 节点 `pytest tests/` → **173 passed**（较事故前 +7）；`ruff check app tests` → **All checks passed**（顺带修掉 2 项存量 E501）。
- 修复后行为：启动自检会**自动清除**残留毒键并记
  `tilacode_user_config_unknown_keys_removed`；若再出现配置非法，日志直接以
  `tilacode_config_invalid`（ERROR）暴露；若任务再次静默，60s 内报
  `task_startup_stalled:60` 并指出排查方向。

### 运维动作（现场节点）

节点升级后**重启服务即可自动清理**；如需立即恢复，手工执行一次：

```bash
rm -f /root/.config/tilacode/tilacode.jsonc && systemctl restart opc-agent-code
```

> 删除用户的 `~/.config/tilacode/tilacode.jsonc` 是安全的：该路径不应由节点写入，
> 节点所需 provider 定义一律写在**任务工作目录**的 `.tilacode/tilacode.jsonc`。
