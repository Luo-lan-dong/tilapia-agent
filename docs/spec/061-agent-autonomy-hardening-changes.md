# 子 Agent 全自主开发能力加固 Changes 变更清单（M1–M7）

> 关联：[ADR-061](../adr/061-agent-autonomy-hardening.md) · [Spec](./061-agent-autonomy-hardening-spec.md) · [Harness](./061-agent-autonomy-hardening-harness.md)
> 规则：**边开发边实时登记**（文件路径 + 变更类型 + 函数/类级明细 + 作用说明），**禁止开发完成后一次性补记**。
> 当前状态：**仅骨架**（本阶段只固化文档，未写任何生产代码）。

## 计划变更点（按模块，实施前逐模块确认）

| 模块 | 预计涉及文件 | 变更类型 | 预计函数/类级明细 | 作用说明 |
|------|-------------|---------|-----------------|---------|
| M1a | `tilacode/packages/core/src/plugin/agent.ts` | 修 | `BUILD_SYSTEM` 常量内容 | 注入验证纪律（改十几行，零控制流） |
| M2 | `opc-central-dispatch-hub/app/services/scheduler.py` 等 | 修 | 派发 payload 携带权限策略 | **策略由中枢统一下发**（D5.1） |
| M2 | `opc-agent-programming/app/executor/task_bridge.py` | 修 | `_task_event_pump` / `_handle_permission_event` 增加兜底拉取 | 漏事件不挂死 |
| M2 | `opc-agent-programming/app/executor/workspace.py` | 修 | `TILACODE_CONFIG_TEMPLATE` 的 permission 改为「工作区内 allow / 工作区外 deny」 | 默认最小权限，不留 `ask` |
| M5a | `opc-central-dispatch-hub/app/api/v1/endpoints/` + 前端输入框 | 增/修 | `@节点` 解析与定向下发 | 中枢↔节点直接对话（D5.3） |
| M5c | `opc-central-dispatch-hub/app/models/` + `opc-web-console/src/**` | 增/修 | 只读镜像表 + 实时刷新 + 上段保持现状 | 实时可见"正在执行什么"（D5.3） |
| M5d | `opc-central-dispatch-hub/app/models/`（新列或新表）+ `app/skills/`（新增 todo 技能）+ `app/skills/registry.py` + `app/agents/orchestrator_prompts.py` | 增/修 | 中枢待办存储 + 编排器 todo 技能 + 提示词引导 | 中枢编排器自己的待办（D5.5） |
| M5d | `opc-web-console/src/components/TaskSummary.tsx` | 修 | 下段渲染中枢待办（与上段视觉分层） | 摘要分两段 |
| M2 | `tilacode/packages/core/src/plugin/agent.ts` | 修（待定） | 默认 ruleset 的 `external_directory` / `*.env` 语义 | 无人值守下语义明确 |
| M3 | `tilacode/packages/core/src/session/runner/llm.ts` | 修 | 回合循环内新增打转检测 + 重试包装 | 防白烧 token |
| M3 | `tilacode/packages/core/src/session/runner/`（新增模块） | 增 | 打转检测 / 重试策略（含上限） | 对齐 V1 能力但加上限 |
| M1b | `tilacode/packages/core/src/tool/{edit,write,apply-patch}.ts` | 修 | 成功分支追加诊断文本 | 编辑后立刻知道写坏 |
| M1b | `tilacode/packages/core/src/lsp/**`（若需移植） | 增 | LSP 服务接线 | **前置勘察后决定是否单独立项** |
| M4 | `opc-central-dispatch-hub/app/agents/spec_builder*.py` | 修 | 生成 `verify.json` | 判据结构化 |
| M4 | `opc-agent-programming/app/executor/task_bridge.py` | 修 | 完成前执行 verify + 分层 + 完成闸 + 失败自修 | 核心闭环 |
| M5a | `opc-central-dispatch-hub/alembic/versions/`（新增迁移） | 增 | `tool_call_logs` check 约束扩展 `llm_usage` | 用量落库 |
| M5a | `opc-central-dispatch-hub/app/services/task_event_handler.py` | 修 | 落库 `tool` / `detail` / `session_id`；写用量记录 | 停止丢弃字段 |
| M5a | `opc-central-dispatch-hub/app/schemas/ws_messages.py` | 增 | `hub.task_steer` 事件 | 中枢→节点收尾指令 |
| M5a | `opc-agent-programming/app/client/hub_client.py` | 修 | `_handle_task_steer` → `send_prompt(delivery="steer")` | 接收收尾指令 |
| M5b | `opc-central-dispatch-hub/app/services/task_service.py` | 修 | 预算判定（80% steer / 100% cancel） | 先软后硬 |
| M5c | 中枢 + `opc-web-console/src/**` | 修/增 | 会话镜像、进度时间线、告警 | 长任务可见（**需先确认前端区域未被并发会话占用**） |
| M6 | `opc-central-dispatch-hub/app/services/experience_service.py` | 修 | `environment_signature` + 召回排序 | 跨机不误伤 |
| M6 | `opc-agent-programming/app/executor/event_translator.py` | 修 | 失败签名经 `detail` 上报 | 节点来源接入 |
| M7 | `opc-web-console/scripts/shoot.mjs`（新增） + `e2e/**` | 增 | 截图脚本 + 文本反馈断言 | 无显示器也能看见页面 |

## 变更清单（实际实施明细）

> 每模块实施时在此新增小节并逐文件登记。**当前为空。**

### M1a · 干活纪律（2026-09-28 **已完成**）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `opc-agent-programming/tilacode/packages/core/src/plugin/agent.ts` | 修 | `BUILD_SYSTEM` 常量：1 句 → 3 段（新增「Work from evidence, not assumption」+「Verify before you report done」+ 收尾报告要求） | 默认 `build` agent 获得验证纪律：改完必须跑 lint/typecheck/测试、不臆测命令与测试框架、失败读原文改因后重跑、不问"要不要跑"、无检查可跑须明说未验证；**控制流零改动** |
| `opc-agent-programming/tilacode/packages/core/test/agent.test.ts` | 增/修 | 新增 `builtInAgents` 装配助手（原 1 处内联装配改为复用）+ 3 项用例 `UT-M1a-1~3` | 固化纪律关键短语、防纪律串到 `explore`/`compaction`、防既有内置提示词被误改 |

### M2a · 权限语义收敛（2026-09-28 **已完成**）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `tilacode/packages/core/src/v1/config/config.ts` | 修 | `Info` 新增 `permission_mode: "interactive" \| "unattended"`（可选） | 配置键必须先在 V1 schema 声明，否则 V1 解析会把它当未知键丢弃 |
| `tilacode/packages/core/src/v1/config/migrate.ts` | 修 | `migrate()` 增加 `permission_mode: info.permission_mode` | 把开关从 V1 带到 V2（V1→V2 迁移是节点配置的唯一通路） |
| `tilacode/packages/core/src/config.ts` | 修 | V2 `Info` 新增同名 `permission_mode` 字段 | V2 侧可读 |
| `tilacode/packages/core/src/permission.ts` | 修 | 新增 `refusesUnanswered()`（读 `Config.latest(documents,"permission_mode")`）；`assert`/`ask` 中 `ask` 且无人值守 → 按 `deny` 处理（抛 `BlockedError`）；层新增 `Config.Service`；`node.deps` 增 `Config.node` | **无人值守下 `ask` 不再无限等待**，一律拒绝；**不改内置 ruleset**，故 tool-output/tmp/plans 白名单与"工作区内免断言"原样保留 |
| `tilacode/packages/core/src/tool/tool.ts` | 增 | `permissionFailure(error, fallback)` 导出 | 把 `BlockedError` 映射为**含匹配规则 + "不要重试"**的 `ToolFailure`；其它错误回退原兜底文案 |
| `tilacode/packages/core/src/tool/{bash,edit,write,apply-patch,read,grep,glob,question,todowrite,webfetch,websearch}.ts` | 修 | 各 `Effect.mapError` 改走 `Tool.permissionFailure` | **拒绝原因回灌**：模型能看到"被策略拦截 + 命中规则 + 不要重试"，不再盲重试 |
| `opc-agent-programming/app/executor/workspace.py` | 修 | `TILACODE_CONFIG_TEMPLATE` 移除 `"permission": {"*": "allow"}`、新增 `"permission_mode": "unattended"`；`_heal_workspace_config` 增加存量迁移 | 修 A-R1（blanket allow 覆盖内置护栏）；并让**已存在的工作区**也完成迁移（仅在恰好等于旧模板默认值时迁移，不误动用户自定义规则） |
| `tilacode/packages/core/test/permission.test.ts` | 增 | 无人值守专用 harness + 2 用例（ask→deny 且不排队；白名单仍放行）+ 1 项 `permissionFailure` 文案用例 | 锁死 M2a 语义 |
| `tilacode/packages/core/test/tool-{read,edit,write,todowrite,question}.test.ts` | 修 | 6 处拒绝断言由"通用文案"改为 `stringContaining("Blocked by permission policy")` | 断言**新的**可读拒绝文案（行为断言不变） |
| `opc-agent-programming/tests/test_workspace.py` | 修 | 4 处断言改为"无 blanket allow + `permission_mode == "unattended"`"；`ensure_tilacode_config_content` docstring 更新 | 同步被有意改变的模板行为 |

**M2a 遗留（登记，未做）**：`tilacode/packages/core/src/tool/skill.ts:97` 的 `unableToLoad(input.name, error)` 未改走 `permissionFailure`（其实现是否已带错误细节**未核实**）；M2b（中枢下发策略）未开始。

### M2b · 中枢下发权限策略（未开始）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| 待登记 | | | |

### M3 · 防打转与会话级重试（2026-09-28 **已完成**）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `tilacode/packages/core/src/session/retry.ts` | **增**（新文件） | `SessionRetry`：`delay(attempt, retryAfterMs)`（provider 显式延迟优先，否则 `2000×2^(n-1)` 封顶 30s）、`decide({attempt, elapsedMs, retryable, retryAfterMs})`、常量 `MAX_ATTEMPTS=5` / `MAX_TOTAL_DELAY_MS=60s` | **有上限**的会话级重试策略（V1 无上限，与"防白烧 token"矛盾） |
| `tilacode/packages/core/src/session/doom-loop.ts` | **增**（新文件） | `SessionDoomLoop`：`threshold=3`、`signature(name, input)`（`name ␀ JSON(input)`）、`hint`、`make()` → 按 session 的滚动窗口 `observe()` | **跨 step** 的重复调用检测（V1 的"同消息内"窗口在 V2 永不触发） |
| `tilacode/packages/core/src/session/runner/llm.ts` | 修 | ① imports `+Duration`、`+SessionDoomLoop/SessionRetry`；② 层状态 `+doomLoop`、`+emptyFileSteps`、`+NO_PROGRESS_STEPS=4`；③ `tool-call` 分支在 settle 前过打转窗口，命中则**不执行**并回灌 `LLMEvent.toolResult({result:{type:"error",value:hint}})`（**必须回一条结果**，否则下一轮 provider 请求会因未应答的工具调用而失败）；④ provider 流外套**有上限重试循环**（仅在"本轮尚未发布任何输出"时重试以防重复流；`ContextOverflow` 不重试、交既有压缩路径；每次尝试前重置 `overflowFailure`）；⑤ `Step.Ended` 处累计"连续无文件变更步数"，达阈值 `Effect.logWarning("session steps produced no file changes")` | 防打转 + 有上限重试 + 无进展**仅记信号** |
| `tilacode/packages/core/test/session-retry.test.ts` | **增**（新文件） | 7 项（不可重试 / 可重试并推进 attempt / 次数上限 / 时间预算上限 / 指数退避与封顶 / provider 显式延迟优先 / 非法值忽略） | `UT-M3-3/4/5` |
| `tilacode/packages/core/test/session-doom-loop.test.ts` | **增**（新文件） | 4 项（第 3 次相同才拦且持续拦 / 参数变化不拦 / 不同工具与不同 session 隔离 / 签名稳定） | `UT-M3-1/2`（检测层） |
| `tilacode/packages/core/test/session-runner.test.ts` | 增 | 2 项集成用例：「重复相同调用不被执行且回灌提示」（`executions` 仅 2 次、第 3 次工具结果含 hint、共 4 轮 provider 请求）与「入参变化不误拦」（3 次全执行） | `UT-M3-1/2`（行为层） |

**M3 已知测试缺口（登记）**：runner 级"重试后成功"的集成用例未写——假客户端 `streamFailure` 对**每次** `stream()` 都生效，无法脚本化"第一次失败、第二次成功"；该语义由 `SessionRetry.decide/delay` 纯函数用例覆盖。无进展信号为日志级，未写日志断言。

### M1b · 编辑后 LSP 回灌（**已推迟**；前置勘察已完成，结论见 ADR-061「M1b · 前置勘察结论」）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| 待登记 | | | |

### M5a-steer · 中枢↔节点定向发言通道（2026-09-28 **已完成**）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `opc-central-dispatch-hub/app/schemas/ws_messages.py` | 增 | `HUB_TASK_STEER = "hub.task_steer"` | 新下行事件（与 `hub.task_cancel` 的区别：**不终结任务**，只影响后续行为） |
| `opc-central-dispatch-hub/app/services/task_service.py` | 增 | `TaskService.steer_task(session, task_id, text)` | 仅 `assigned`/`running` 可发言；未分配节点/空文本/**节点离线**均明确报错（**不静默丢弃**）；**不改任务状态**；写 `task_steer_sent` 审计日志 |
| `opc-central-dispatch-hub/app/schemas/task.py` | 增 | `TaskSteerRequest{text: 1..4000}`（`extra="forbid"`） | 请求体校验 |
| `opc-central-dispatch-hub/app/api/v1/endpoints/tasks.py` | 增 | `POST /api/v1/tasks/{task_id}/steer` | 发言入口；状态不允许/节点离线 → 409 `TASK_STATE_NOT_ALLOWED` |
| `opc-central-dispatch-hub/tests/services/test_task_steer.py` | **增**（新文件） | 6 项：running 发言成功且**状态不变**、非执行态拒绝、空文本拒绝、未分配节点拒绝、节点离线**显式失败**、任务不存在 404 | 中枢侧验收 |
| `opc-agent-programming/app/client/hub_client.py` | 增 | `_TASK_STEER_EVENT`、`_handle_message` 分发分支、`_handle_task_steer(payload)` | 校验 `task_id`/`text`；任务不在本节点执行则忽略；`bridge.steer` 异常**不外抛**（任务照常继续） |
| `opc-agent-programming/app/executor/task_bridge.py` | 增/修 | 新增 `steer(task_id, text)`；新增 `_last_progress_value` 跟踪（`_forward_progress` 记录、任务收尾清理） | `steer` 复用 `interrupt` 的会话归属约定 `ses_task_{base_task_id or task_id}`，调 `send_prompt(delivery="steer")`（节点侧既有能力）；随后用**已知进度值**上报一条「已收到中枢指令：…」作为**送达确认**（未知进度时不发，避免把进度条打回 0；`stage`/`todos` 传 None 由中枢保持原值） |
| `opc-agent-programming/tests/test_hub_client_reconnect.py` | 增 | 5 项：命中在跑任务→调 `bridge.steer` 且**任务未中断**、目标不符忽略、无效 payload 忽略、无桥接忽略、`steer` 抛异常不外抛 | 节点侧验收 |

**验证**：中枢 `test_task_steer.py` **6/6 通过**（另跑 `test_task_cancel.py` 无回归）；节点 `test_hub_client_reconnect.py` **22/22 通过**、节点全量 `pytest` **187 通过**；`ruff` 本次涉及文件**全过**（中枢另有 3 处 ruff 报错在 `resource_sync_service.py` / `terminal_service.py`，属**既有文件、非本次改动**，未动）。**旧节点兼容**已验证通路：未知事件在 `hub_client.py:584-585` 落到 `logger.debug("unhandled_event")`，无白名单拦截 → 旧节点安全忽略。

**已知测试缺口（登记）**：① `TaskBridge.steer` 的**桥接级**用例未写（会话 ID 约定与送达确认仅靠与已测 `interrupt` 逐行同构保证）；② `POST /{task_id}/steer` 的 **API 级**用例未写（服务级已覆盖）。

### M5a · 用量落库 + 字段不再丢弃（2026-09-28 **已完成**）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `opc-central-dispatch-hub/app/models/tool_call_log.py` | 增 | `ToolCallLogKind.LLM_USAGE = "llm_usage"`；`chk_tool_call_logs_kind` 白名单补 `'llm_usage'` | 用量行有合法 kind |
| `opc-central-dispatch-hub/alembic/versions/20260928c_llm_usage_kind.py` | **增**（新迁移） | `upgrade`：drop→create CHECK；`downgrade`：先把 `llm_usage` 归位为 `agent_task_event`（保留数据）再收紧 | 约束放宽；down_revision=`20260928b_requeue_pending` |
| `opc-central-dispatch-hub/alembic/versions/20260928d_task_progress_tool.py` | **增**（新迁移） | `tasks.progress_tool VARCHAR(64) NULL` | UT-M5-4：当前工具名落库 |
| `opc-central-dispatch-hub/app/models/task.py` | 增 | `Task.progress_tool` | 同上 |
| `opc-central-dispatch-hub/app/schemas/task.py` | 增 | `TaskResponse.progress_tool` | 落库可查（对外可读） |
| `opc-central-dispatch-hub/app/services/task_event_handler.py` | 修 | `_log_cache_metrics()` → `_parse_usage_metrics()`（纯解析）+ `_record_cache_metrics(task_id, node_id, detail, tool)`（记日志 **+** 落库 `llm_usage`）；新增 `_sanitize_progress_tool()`；`_handle_task_progress` 存 `task.progress_tool` | 用量落库 + 停止丢弃 `tool` |
| `opc-central-dispatch-hub/tests/services/test_task_event_handler.py` | 增 | 5 项：`UT-M5a-3`（落库字段逐项断言）、`UT-M5a-4`（无/零/非法 detail 均不落库）、`UT-M5a-5`（CHECK 覆盖枚举全部取值）、`UT-M5a-6`（`progress_tool` 落库）、`UT-M5a-7`（tool 非法忽略/超长截断且进度照常落库） | 中枢侧验收 |

**验证**：`test_task_event_handler.py` **41 passed**；`test_tool_call_log_service.py` + `test_task_steer.py` + `test_task_cancel.py` **20 passed**；涉及文件 `ruff` 全过；
两个迁移均已在库生效（`alembic_version = 20260928d_task_progress_tool`，`tasks.progress_tool varchar(64)` 与含 `llm_usage` 的 CHECK 经 `information_schema`/`pg_constraint` 实证）；
真实落库探针：`tool_call_log_service.record(kind="llm_usage", …)` 实际插入 1 行且字段正确，探针数据已清理（证明约束接受新 kind，而非仅模型层通过）。

**关键设计取舍**：① 落库走 `tool_call_log_service.record`（独立会话 + 内部兜底）而非进度更新同一事务 —— 指标是**旁路**，写失败不得回滚 `progress`；
② 零样本（`input==0 and cache_read==0`）不落库，避免噪音行；③ **不猜 `session_id`**（桥接会话名约定 `ses_task_{base_task_id or task_id}` 在子任务下会失真），只写 `task_id` + `args_summary.agent_id`；
④ `name="agent_llm_usage"`：节点 `detail` 里没有模型名，不编造，模型维度待节点侧补字段。

**明确不做**：预算闸判定（M5b）、用量曲线/告警前端（M5c）、节点侧新增字段（模型名/轮次）、历史日志行回填（无数据源）。

### M2b · 中枢下发权限策略（2026-09-28 **已完成**）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `opc-central-dispatch-hub/app/models/system_settings.py` | 增 | `permission_policy`（JSONB） | 全局单条策略（Q1）；NULL = 未配置 = 最严 |
| `opc-central-dispatch-hub/alembic/versions/20260928e_permission_policy.py` | **增**（新迁移） | `upgrade` 加列 / `downgrade` 删列 | down_revision=`20260928d_task_progress_tool` |
| `opc-central-dispatch-hub/app/services/permission_policy_service.py` | **增**（新文件） | `default_policy` / `normalize_policy` / `load_policy` / `save_policy` / `audit_summary` | 归一化（**丢 blanket allow**、逐条剔除非法、上限）+ 读写 + 审计摘要 |
| `opc-central-dispatch-hub/app/schemas/system_settings.py` | 增 | `PermissionPolicyAllowEntry` / `PermissionPolicyResponse` / `PermissionPolicyUpdate`（`extra="forbid"`） | API 契约 |
| `opc-central-dispatch-hub/app/api/v1/endpoints/settings.py` | 增 | `GET` / `PUT /api/v1/settings/permission-policy` | 读取与更新（管理台 UI 归 M5c） |
| `opc-central-dispatch-hub/app/services/scheduler.py` | 修 | `_assign_plan` 整批读一次策略并写入每条 payload；审计行追加策略摘要 | 自动派发携带策略（IT-A1 半边） |
| `opc-central-dispatch-hub/app/services/task_service.py` | 修 | 手动派发挂载策略 + 审计追加 | 手动/重派同样携带策略 |
| `opc-central-dispatch-hub/tests/api/v1/test_settings.py` | 增 | 4 项（默认最严 / PUT-GET 一致 / blanket 被丢弃 / 额外字段 422）+ 快照恢复夹具 | 中枢侧验收 |
| `opc-central-dispatch-hub/tests/api/v1/test_task_lifecycle.py` | 修 | 两处 WS 派发断言策略随行下发 | 端到端下发验证 |
| `opc-agent-programming/app/executor/permission_policy.py` | **增**（新文件） | `normalize_policy` / `apply_to_config` / `effective_policy_from_config` / `wildcard_match` / `is_allowed` / `deny_reason` | 节点侧落地与判定（镜像 tilacode 通配符） |
| `opc-agent-programming/app/models/schemas.py` | 增 | `TaskDispatchPayload.permission_policy` | 协议字段 |
| `opc-agent-programming/app/executor/workspace.py` | 修 | `ensure_tilacode_config(dir, policy=None)` → 应用策略并返回生效策略 | 派发即权威；无策略=沿用缓存 |
| `opc-agent-programming/app/executor/task_bridge.py` | 修 | `_session_policies` + `_session_policy()` + `_handle_permission_event` 策略驱动 | **修掉无条件回 `once` 的放行口**；未命中→`reject`+原因 |
| `opc-agent-programming/app/executor/tilacode_client.py` | 修 | `permission_reply(..., message=None)` | 拒绝原因随回复下发 |
| `opc-agent-programming/tests/test_permission_policy.py` | **增**（新文件） | 7 项 | 归一化/落地/回读/判定/原因 |
| `opc-agent-programming/tests/test_workspace.py` | 增 | 3 项（策略写入、缓存语义、历史 blanket 清除） | 工作区落地验收 |
| `opc-agent-programming/tests/test_task_bridge.py` | 修/增 | 原「无条件 once」用例改写为 3 项（命中→once、未命中→reject+原因、字段缺失→fail-closed） | 判定验收 |

**验证**：中枢 `test_settings.py` **11 passed**、`test_task_lifecycle.py` **4 passed**（含 WS 端到端策略断言）；节点 `pytest tests` **200 passed**；两侧 `ruff` 全过；迁移在库生效。
**安全要点**：① 节点此前对**每个**权限询问无条件回 `once`（默认实参），等于绕过全部 deny 护栏 → 现改为策略驱动 + `reject` 带原因；
② `is_allowed` 曾拟加 `fnmatch` 兜底，因语义比 tilacode 宽（`[...]`）会**放宽**权限而删除；③ blanket allow 三处防线（中枢归一化 / 节点应用 / 模板）。

### M5d · 中枢编排器待办（2026-09-28 **已完成**）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `opc-central-dispatch-hub/app/models/task_orchestrator_todo.py` | **增**（新文件） | `TaskOrchestratorTodo` + 状态/长度/条数常量 | 任务级中枢待办表；`status` CHECK、`(task_id, seq)` 索引；与 `tasks.todos` **物理分离** |
| `opc-central-dispatch-hub/app/models/__init__.py` | 修 | 注册 `TaskOrchestratorTodo` | 元数据可见 |
| `opc-central-dispatch-hub/alembic/versions/20260928f_orchestrator_todos.py` | **增**（新迁移） | 建表 + 2 索引 | down_revision=`20260928e_permission_policy` |
| `opc-central-dispatch-hub/app/services/orchestrator_todo_service.py` | **增**（新文件） | `list_todos` / `add_todos` / `update_todo` / `remove_todo` / `render_lines` / `OrchestratorTodoError` | 读写层：内容 1..500、单任务 ≤50、`seq` 追加、状态白名单、`completed_at` 同步、跨任务过滤 |
| `opc-central-dispatch-hub/app/skills/orchestrator_todo.py` | **增**（新文件） | `OrchestratorTodoSkill`（`todo_write`；`add/start/complete/update/remove/list`）+ `_resolve_task_id` | 任务由 `session_id→TaskBuildingSession.task_id` 反查、回退 `parent_task_id`；未绑定任务明确失败 |
| `opc-central-dispatch-hub/app/skills/registry.py` | 修 | 注册 `OrchestratorTodoSkill` | 技能数 23 → **24** |
| `opc-central-dispatch-hub/app/schemas/task.py` | 增 | `OrchestratorTodoItem`（`from_attributes`）/ `OrchestratorTodoListResponse` | 下段 API 契约（含 pending/completed 计数） |
| `opc-central-dispatch-hub/app/api/v1/endpoints/tasks.py` | 增 | `GET /api/v1/tasks/{task_id}/orchestrator-todos` | 只读端点；任务不存在 404 `201001` |
| `opc-central-dispatch-hub/tests/skills/test_orchestrator_todo_skill.py` | **增**（新文件） | 8 项 | 顺序/状态迁移/物理分离/未绑定任务/上限/非法入参/`parent_task_id` 兜底 |
| `opc-central-dispatch-hub/tests/api/v1/test_tasks.py` | 增 | 2 项 | 端点升序+计数；空态 + 404 |
| `opc-web-console/src/types/task.ts` | 增 | `OrchestratorTodo` / `OrchestratorTodoListResponse` | 前端类型 |
| `opc-web-console/src/api/tasks.ts` | 增 | `fetchOrchestratorTodos(taskId)` | 下段取数 |
| `opc-web-console/src/hooks/useTaskPlanTracking.ts` | 修 | 同轮 `Promise.all` 拉详情+待办；返回 `orchestratorTodos` | 下段失败**不清空**已有清单；终态停止轮询行为不变 |
| `opc-web-console/src/components/TaskSummary.tsx` | 增 | `OrchestratorTodoSection`（标题/计数/条目/空态）+ `orchestratorTodos` 可选 prop | 摘要**下段**；上段现有 6 区块**未改动**，仅在其后追加 |
| `opc-web-console/src/components/TaskNewRightPanel.tsx` | 修 | 透传 `orchestratorTodos` | 面板接线 |
| `opc-web-console/src/pages/TaskNew.tsx` | 修 | 取用并透传 `orchestratorTodos` | 页面接线 |
| `opc-web-console/src/pages/TaskNew.css` | 增 | `.task-summary__orchestrator*` | 下段样式（全 `var(--*)` 语义变量，符合 ADR-052） |
| `opc-web-console/src/__tests__/components/TaskSummary.test.tsx` | 增 | 2 项 | 上下两段并存不串位（含 `1/2` 计数）；中枢无待办时空态明确 |

**验证**：中枢技能测试 **8 passed**、端点测试 **2 passed**、涉及文件 `ruff` 全过；前端 `TaskSummary.test.tsx` **7 passed**；迁移在库生效（`alembic_version = 20260928f_orchestrator_todos`）。
**前端全量基线（登记，非本次引入）**：`npx vitest run` = **71 passed / 5 failed（3 文件）**，失败均为既有漂移或另一工作流在途改动——
① `TaskNewInputBar.test.tsx` 期望的 `send-button-busy` 在组件中已不存在；②/③/④ `TaskNew.test.tsx` 期望的 placeholder 与组件现有文案不一致（组件在 `TaskNewChatPanel.tsx:372`）；
⑤ `TaskDetailDrawer.test.tsx` 对应的 `TaskDetailDrawer.tsx` 正被另一工作流修改（`git status` 显示 M）。`npx tsc -b` 的 2 处既有类型错误同理（`PlanReviewPanel.tsx`、`useTaskPlanActions.ts`）。

### M4 · 可执行验收 + 完成闸 + 失败自修（2026-09-28/29 **已完成**，1 项前端缺口）

> 用户裁决：**Q6 判据由中枢编排器 LLM 生成** · **Q7 新增任务状态 `verify_blocked`** · **Q8 两者结合**（节点先自修 ≤5 轮，用尽后中枢介入找用户决定）。
> 阶段一 = 判据结构化 + 分层执行器 + 完成闸 + 阻断上报 + 24h 兜底 + 决策入口；阶段二 = 自修轮次与前端决策 UI 的细化与专门用例。

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `opc-central-dispatch-hub/app/services/verify_plan.py` | **增**（新文件） | `normalize_verify_plan` / `must_pass_checks` / `summarize_plan` / `LAYER_TIMEOUTS` / `DEFAULT_MUST_PASS` | 判据归一化：层白名单 L0~L4、条数/长度上限、**必过层规则固化**、每层超时 30s/60s/5min/10min/20min |
| `opc-central-dispatch-hub/app/schemas/task_building.py` | 修 | `TaskSpecification.verify: dict \| None` | 判据随三件套一起存 JSONB、随派发下发（调度器无需改动） |
| `opc-central-dispatch-hub/app/agents/spec_builder_prompts.py` | 增 | `DOC_VERIFY_SYSTEM_PROMPT` | 判据轮提示词（分层含义 + 只写确信存在的检查 + 无检查输出空数组） |
| `opc-central-dispatch-hub/app/agents/spec_builder.py` | 修/增 | 第 4 轮判据生成 + `parse_verify_json()` 容错解析 + `_build_verify_messages()` | 判据生成失败**不丢三件套**（置 None → 节点按「无法验证」上报） |
| `opc-central-dispatch-hub/app/skills/task_creation.py` / `deploy_plan.py` | 修 | 创建任务时携带归一化判据；部署清单事件保留判据 | 判据随任务落库，不被其它技能丢字段 |
| `opc-agent-programming/app/executor/verify_runner.py` | **增**（新文件） | `load_plan` / `from_project_config` / `derive_checks` / `run_plan` / `_run_check` / `CheckResult` / `VerifyReport` | 节点分层执行器：判据优先级 `verify.json` > 项目配置 > 推导；非 0/超时/异常一律按**未通过**；输出尾部截断保留 |
| `opc-agent-programming/app/executor/workspace.py` | 修 | `_SPEC_DOC_PATHS` 增 `verify.json`；写入器对 dict 走 `json.dumps`；`TaskSpecification` 协议增 `verify` | 判据落盘到 `specs/<版本目录>/verify.json` |
| `opc-agent-programming/app/models/schemas.py` | 修 | `TaskSpecification.verify: dict \| None` | 协议字段 |
| `opc-agent-programming/app/executor/task_bridge.py` | 修/增 | `BridgeCallbacks.on_verify_blocked`；`_run_completion_gate()` / `_build_repair_prompt()` / `_report_verify_blocked()`；`_dispatch_result` 接闸 | **完成闸**：必过层全绿才 `completed`；红 → 自修 ≤5 轮后复验；**无法验证 → 不自修直接阻断**；`on_completed` 增 `verify_report` |
| `opc-agent-programming/app/client/hub_client.py` | 修/增 | `report_verify_blocked()`；`_TASK_VERIFY_BLOCKED_EVENT`；`report_completed(verify_report=)` | 阻断上报与验收报告回传 |
| `opc-agent-programming/app/main.py` | 修 | 注入 `on_verify_blocked` | 回调接线 |
| `opc-central-dispatch-hub/app/models/task.py` | 修 | `TaskStatus.VERIFY_BLOCKED`；`ACTIVE_STATUSES` 纳入；`verify_report` / `verify_blocked_at` 两列 | 阻断态与 24h 计时字段 |
| `opc-central-dispatch-hub/alembic/versions/20260928g_verify_blocked.py` | **增**（新迁移） | 加两列 + 扩展 `status` CHECK（down_revision=`20260928f_orchestrator_todos`） | 已在库生效 |
| `opc-central-dispatch-hub/app/schemas/ws_messages.py` | 修 | `AGENT_TASK_VERIFY_BLOCKED` + `TaskVerifyBlockedPayload` + `TaskCompletedPayload.verify_report` | 事件协议 |
| `opc-central-dispatch-hub/app/services/task_event_handler.py` | 增 | `_handle_task_verify_blocked()` + `_append_decision_message()` + 分发表注册 | 置阻断态、落验收报告、**在任务构建会话追加「等你决定」消息**、写审计 |
| `opc-central-dispatch-hub/app/services/scheduler.py` | 增 | `fail_expired_verify_blocked_tasks()` + `_fail_expired_verify_blocked()` + 调度循环调用 | **24h 无回应兜底置 failed**（单条 UPDATE，写明原因） |
| `opc-central-dispatch-hub/app/services/task_service.py` | 增 | `decide_verify_blocked(session, task_id, decision, note)` | `accept` → 完成并记录；`retry` → 回 running 并把失败摘要 steer 给节点 |
| `opc-central-dispatch-hub/app/schemas/task.py` | 增 | `TaskVerifyDecisionRequest{decision: accept\|retry, note?}` | 决策请求体 |
| `opc-central-dispatch-hub/app/api/v1/endpoints/tasks.py` | 增 | `POST /api/v1/tasks/{task_id}/verify-decision` | 决策入口（非阻断态 409） |
| `opc-agent-programming/tests/test_task_bridge.py` | 修 | `on_completed` 假实现接受 `verify_report`；**新增 autouse 夹具把闸门替换为「总是放行」** | 既有 7 个完成路径用例聚焦原意图；闸门自身行为另建专门用例 |

**验证（已完成）**：节点 `ruff` 全过、`pytest tests` **216 passed**（含新增 `test_verify_runner.py` 12 项与闸门 4 项）；
中枢 `ruff` 全过、`test_verify_plan.py` **6 passed**、`test_task_verify_blocked.py` **6 passed**；
`alembic` 单 head 且 `20260928g_verify_blocked` 已在库生效。
**完成闸生效的直接证据**：接线后节点套件曾出现 7 个「完成路径」用例失败（无判据 → 无法验证 → 阻断）；将该文件闸门替换为「总是放行」夹具后回归全绿，闸门行为改由专门用例覆盖。
**环境说明**：分层执行器的真实子进程用例在本机沙箱需放宽（`spawn EPERM`，已按规则升级一次执行）；生产节点进程无此限制。
**已知缺口（登记）**：① ~~前端 `verify_blocked` 状态标签与决策按钮~~ **已补齐**（`TaskSummary` 决策区 + `decideVerifyBlocked` API + `TaskNew` 接线；`TaskSummary.test.tsx` **9 passed**，含 2 项决策 UI 用例）；② `IT-A3` 与 `E2E-A1` 未执行；③ 自修多轮上下文细化留待优化。

**下一步**：M5b 预算闸（M）。


| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| 待登记 | | | |

### M5b · 预算闸（2026-09-29 **已完成**）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `opc-central-dispatch-hub/app/services/budget_policy_service.py` | **增**（新文件） | `normalize_policy` / `resolve_budget` / `load_policy` / `save_policy` / `usage_for_task` / `evaluate` / `wrap_up_instruction` / `budget_exceeded_reason` / `needs_action` | 预算归一化（边界夹取）、任务级按字段覆盖、用量聚合（steps=用量行数、tokens=Σ(input+output)）、双维度水位判定与**幂等守卫** |
| `opc-central-dispatch-hub/app/models/system_settings.py` | 增 | `budget_policy`（JSONB 全局默认） | 全局预算配置（Q9） |
| `opc-central-dispatch-hub/app/models/task.py` | 增 | `budget`（任务级 override）+ `budget_state`（幂等状态） | 任务级覆盖与每阈值只触发一次 |
| `opc-central-dispatch-hub/alembic/versions/20260928h_budget_policy.py` | **增**（新迁移） | 上述三列 | down_revision=`20260928g_verify_blocked`；已在库生效 |
| `opc-central-dispatch-hub/app/services/scheduler.py` | 增 | `enforce_task_budgets()` + `_run_budget_sweep()` + 调度循环调用 | 软→`steer_task(收尾指令)`（**不停任务**）；硬→`cancel_task(预算耗尽)`；软通知未送达不标记、下轮重试 |
| `opc-central-dispatch-hub/app/services/task_service.py` | 修 | `cancel_task(..., reason=)`；两处创建任务路径写入 `budget` | 硬停原因可写「预算耗尽」；任务级预算落库 |
| `opc-central-dispatch-hub/app/schemas/system_settings.py` + `app/api/v1/endpoints/settings.py` | 增 | `BudgetPolicyResponse/Update` + `GET|PUT /api/v1/settings/budget-policy` | 预算配置入口 |
| `opc-central-dispatch-hub/app/schemas/task.py` | 增 | 创建请求 `budget`；响应 `budget`/`budget_state` | 可设置 + 可观测 |
| `opc-central-dispatch-hub/tests/services/test_budget_gate.py` | **增**（新文件） | 7 项 | 边界/覆盖/双维度/幂等/聚合/软硬两档/失败重试 |

**验证**：`test_budget_gate.py` **7 passed**；`ruff` 全过；`import app.main` 通过；`alembic` 单 head（`20260928h_budget_policy`）且迁移已在库生效。
**勘察修正（登记）**：`tool_call_logs.result_summary` 是 **Text 列（JSON 字符串）**而非 JSONB——首版按 dict 解析在真实库当场失败，由新测试暴露并修正（服务侧兼容 str/dict，测试按 `json.dumps` 写入）。
**已知缺口**：前端预算/用量展示归 M5c；差异化预算与节点侧兜底未做；`E2E-A2` 未执行。

**下一步**：M5c 只读镜像 + 会话视图 + 时间线 + 用量告警（L）。

### M5d · 中枢编排器待办（未开始，**全新能力**）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| 待登记 | | | |

### M5c · 只读镜像 + 会话视图 + 时间线 + 用量告警（2026-09-29 **已完成**）

> 裁决：**Q12 动作级摘要时间线**（不存原始工具输出全文）· **Q13 按需计算 + 仪表盘聚合**（阈值用已定基线，不引入外部通知）。

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `opc-central-dispatch-hub/app/services/task_insight_service.py` | **增**（新文件） | `usage_points` / `summarize_usage` / `compute_alerts` / `build_timeline` / `collect_task_insight`；常量 `CACHE_HIT_BASELINE=0.952`、`REPEATED_ACTIVITY_LIMIT=3`、`NO_FILE_WRITE_STEPS=4`、`MIN_HIT_RATE_SAMPLES=5` | 用量曲线/汇总（与基线对比）、**三条告警规则**（样本不足不告警）、动作级时间线（人类可读标题） |
| `opc-central-dispatch-hub/app/services/task_event_handler.py` | 修 | `_handle_task_progress`：工具/阶段**变化**时写 `tool_call_logs(kind=agent_task_event, name=tool_activity)` | 动作历史留痕（无新节点协议，天然节流） |
| `opc-central-dispatch-hub/app/services/task_service.py` | 修 | `steer_task` 成功后写 `steering/task_steer_sent` 事件 | 中枢发言进入时间线（离线可回看） |
| `opc-central-dispatch-hub/app/schemas/task.py` | 增 | `TaskTimelineEntry` / `TaskUsagePoint` / `TaskUsageSummary` / `TaskInsightAlert` / `TaskInsightResponse` | 只读洞察契约 |
| `opc-central-dispatch-hub/app/api/v1/endpoints/tasks.py` | 增 | `GET /api/v1/tasks/{task_id}/insight` | 一次取回用量曲线 + 汇总 + 告警 + 时间线（任务不存在 404） |
| `opc-central-dispatch-hub/tests/services/test_task_insight.py` | **增**（新文件） | 6 项 | 曲线/汇总、三条告警各自触发与**不误报**、时间线标题、端到端聚合 |
| `opc-central-dispatch-hub/tests/conftest.py` | 修 | 清理顺序：`TaskOrchestratorTodo` 先于 `Task` 删除；`_GUARDED_TABLES` 纳入该表 | **修掉新 FK 表导致的清理/恢复外键冲突**（M5d 引入的表） |

**验证（M5c-1）**：`test_task_insight.py` **6 passed**；`ruff` 全过；`import app.main` 通过。
**顺带修复（登记）**：`conftest` 的快照/恢复与 `db_session` 清理原先会因新表外键冲突而报错（偶发取决于残留数据）——已按「引用 tasks 的子表排在 Task 之后」修正，并清理了 M5d 测试残留行。

**M5c-2 前端（已完成）**

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `opc-web-console/src/components/TaskInsightPanel.tsx` | **增**（新文件） | `TaskInsightPanel` | 告警条 + 用量汇总（含 95.2% 基线对比进度条）+ 最近动作时间线（最新在前）+ `@节点` 发言输入框 |
| `opc-web-console/src/components/TaskSummary.tsx` | 修 | 追加 `<TaskInsightPanel>`；接受 `insight`/`onSteer` | **上段 6 区块与下段中枢待办均不改动**，只做追加（UT-M5-7 继续成立） |
| `opc-web-console/src/hooks/useTaskPlanTracking.ts` | 修 | 同轮 `Promise.all` 增取 `fetchTaskInsight`；返回 `insight` | 实时刷新；洞察失败**不清空**已有内容 |
| `opc-web-console/src/api/tasks.ts` | 增 | `fetchTaskInsight()` / `steerTask()` | 只读洞察与 `@节点` 发言入口 |
| `opc-web-console/src/types/task.ts` | 增 | `TaskInsight` / `TaskUsagePoint` / `TaskUsageSummary` / `TaskInsightAlert` / `TaskTimelineEntry` | 前端类型 |
| `opc-web-console/src/components/TaskNewRightPanel.tsx` / `src/pages/TaskNew.tsx` | 修 | 透传 `insight`/`onSteer`；`handleSteerTask` | 接线（发言成功后重查以满足提示） |
| `opc-web-console/src/pages/TaskNew.css` | 增 | `.task-insight*` | 全 `var(--*)` 语义变量（ADR-052） |
| `opc-web-console/src/__tests__/components/TaskInsightPanel.test.tsx` | **增**（新文件） | 6 项 | 无数据不渲染、用量汇总与基线提示、告警与时间线倒序、无用量空态、发言回调并清空、无回调不渲染输入框 |
| `opc-central-dispatch-hub/app/services/dashboard_service.py` | 修 | `_collect_alerts` 追加「预算接近上限 / 已按预算强制停止」告警 | 仪表盘聚合（Q13）：**只按 `budget_state` 单列过滤**，不做跨表聚合 |

**验证（M5c-2）**：`TaskInsightPanel.test.tsx` **6 passed** + `TaskSummary.test.tsx` **9 passed**；颜色门禁**无新增违规**（唯一告警仍是既有 `App.tsx:57`）；中枢 `ruff` 全过、`import app.main` 通过。
**已知缺口（登记）**：① 仪表盘预算/用量告警**未补专门用例**；② `E2E-A3/A6/A7` 未执行。

### M6 · 经验记忆下沉与跨机复用（2026-09-29 **已完成**）

> 裁决：**Q14 节点侧计算环境签名 + 心跳上报** · **Q15 作为元规则文档随派发下发**（复用既有 materialize 链路，零协议改动）。

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `opc-central-dispatch-hub/app/models/agent_node.py` | 增 | `environment_signature`（String(64)） | 节点环境签名（心跳上报） |
| `opc-central-dispatch-hub/app/models/experience_record.py` | 增 | `source_node_id` + `environment_signature` | 经验条目的来源机器与环境快照（跨机匹配键） |
| `opc-central-dispatch-hub/alembic/versions/20260928i_experience_env.py` | **增**（新迁移） | 三列 + `idx_experience_records_env_status` | down_revision=`20260928h_budget_policy`；已在库生效 |
| `opc-central-dispatch-hub/app/services/agent_node_ws_service.py` | 修 | `_handle_heartbeat` 落 `environment_signature` | 心跳上报即入库（旧节点不报则保持原值） |
| `opc-central-dispatch-hub/app/services/experience_service.py` | 增/修 | `record_failure(..., source_node_id=, environment_signature=)`；**新增** `recall_for_dispatch()` / `render_dispatch_doc()` / `record_hits()` | 失败记录带来源与环境；**同环境优先、无匹配才回退「环境未知」**；召回渲染为文档；召回即计命中 |
| `opc-central-dispatch-hub/app/services/standard_doc_service.py` | 修 | `build_rule_docs_payload(session, task, node=None)` 增第 4 段「历史经验」（`target=meta-rule`、`file_name=experiences`） | **派发前召回注入**：经验落 `.tilacode/rules/experiences.md` 并进 AGENTS.md 索引；召回/计数异常**不阻断派发** |
| `opc-central-dispatch-hub/app/services/scheduler.py` / `task_service.py` | 修 | 两处派发点传入目标节点 | 自动/手动派发都按目标节点环境召回 |
| `opc-agent-programming/app/env_signature.py` | **增**（新文件） | `compute_environment_signature()`（`lru_cache`） | 节点侧签名：OS+架构+Python 版本+平台+Node/Bun 主版本 → sha256 前 16 位；支持 `TILAPIA_ENV_SIGNATURE` 覆盖；**探测失败返回 None**（不阻断） |
| `opc-agent-programming/app/client/hub_client.py` | 修 | 心跳 payload 增 `environment_signature` | 上报签名 |
| `opc-agent-programming/tests/test_env_signature.py` | **增**（新文件） | 3 项 | 稳定性/长度、环境变量覆盖、探测失败容错 |
| `opc-central-dispatch-hub/tests/services/test_experience_env.py` | **增**（新文件） | 6 项 | 来源与环境落库、同环境优先、**异环境不被召回**、回退「环境未知」、命中计数、文档三要素 |

**验证（M6 全部）**：节点 `ruff` 全过 + `test_env_signature.py` **3 passed**；中枢 `ruff` 全过 + `test_experience_env.py` **6 passed** + `test_experience_consolidation.py` **4 passed**；`import app.main` 通过；迁移已在库生效。
**定时整理（本轮补齐）**：`app/agents/experience_consolidator.py`（`parse_draft` 容错解析 + `consolidate` 批量起草）+ `experience_service.list_consolidation_candidates()` / `start_draft()` + 调度循环每日守卫 `_consolidate_experiences_if_due()`；**只写 `status="draft"`、不生效**，草稿不进召回，人工确认后才 resolved；LLM 失败保持 pending。
**已知缺口**：`IT-A5`（真实链路集成）待与 G3 同批执行。




| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| 待登记 | | | |

### M7 · 前端视觉反馈（2026-09-29 **代码完成**；依赖烘焙待构建期）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `opc-agent-programming/tools/frontend_probe.mjs` | **增**（新文件） | `parseArgs` / `loadPlaywright` / `main` | 浏览器探针：文本优先（结构化 JSON：状态/标题/结构快照/文本摘要/控制台错误/失败请求），`--shots` 才截图；**退出码即判据**（0/1/2/3）；`node --check` 通过 |
| `opc-agent-programming/tools/package.json` + `tools/README.md` | **增**（新文件） | — | `playwright` 依赖声明 + 预装命令 + 退出码语义 + `TILAPIA_TOOLS_DIR` / `PLAYWRIGHT_BROWSERS_PATH=0` 说明 |
| `opc-agent-programming/app/executor/workspace.py` | 修/增 | `TILACODE_CONFIG_TEMPLATE.instructions` +4 项；`_heal_workspace_config` 补全必载指令；`ensure_frontend_tooling()`；`FRONTEND_VERIFICATION_RULE` | 探针与前端验证规则落 `.tilacode/`（产物扫描已排除），规则被 tilacode 加载 |
| `opc-agent-programming/app/executor/task_bridge.py` | 修 | `_prepare_session` 调 `ensure_frontend_tooling()` | 每次派发确保前端工具就位 |
| `opc-agent-programming/tests/test_workspace.py` | 增/修 | +1 项；原不覆盖用例语义更新 | 落盘 + instructions 补全 + 幂等 |

**验证**：节点 `ruff` 全过；`test_workspace.py` **24 passed**；节点全量 **217 passed / 3 failed**（3 项为已知沙箱 spawn EPERM）。
**待办**：构建期烘焙（`npm ci` + `npx playwright install chromium`，`PLAYWRIGHT_BROWSERS_PATH=0`）与 `TILAPIA_TOOLS_DIR` 注入（与 G2 同批）；`UT-M7-2/3/4` 真实链路与截图产物验证。

### 2026-10-02 · 用户三问题修复批次（步骤收尾 / 历史会话恢复 / 待办两层）

**问题来源**（用户 2026-10-02 反馈）：① 规范文档已生成但步骤卡一直「执行中」；② 历史会话在网络不稳时加载不出来、切页回来恢复失败；
③ 子节点不返回待办明细、中枢有时不先列 todo；要求任务摘要明确上下两层（上=子 Agent、下=中枢）。

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `opc-central-dispatch-hub/app/services/task_build_rounds.py` | **增**（新文件） | `BuildRoundCollector`（`add_thinking`/`start_step`/`finish_step`/`close_round`/`_find_step`）、`finalize_running_steps` | 回合收集状态机抽出：步骤按 step_id 建索引，`step_finished` **跨回合**更新；落库前 running 步骤收尾为 failed |
| `opc-central-dispatch-hub/app/api/v1/endpoints/task_building.py` | 修 | `_task_build_event_stream` 改用 `BuildRoundCollector`（移除 `current_round`/`_ensure_round`）；`_persist_chat_exchange` 落库前调用 `finalize_running_steps` | **根因修复**：回合分割后结束事件丢失 → 步骤卡不再永远「执行中」 |
| `opc-central-dispatch-hub/app/agents/orchestrator_prompts.py` | 修 | `ORCHESTRATOR_BASE_PROMPT` 规则 3 | 创建任务后、`smart_dispatch` 前先 `todo_write(action=add)` 列中枢计划，推进用 start/complete |
| `opc-central-dispatch-hub/tests/services/test_task_build_rounds.py` | **增**（新文件） | 3 项 | 跨回合收尾 / 回合闭合 / running 收尾单测 |
| `opc-central-dispatch-hub/tests/api/v1/test_task_building.py` | 增 | +2 项 | 回合分割后 `step_finished` 仍生效；结束事件丢失的步骤落库为 failed |
| `opc-central-dispatch-hub/tests/agents/test_orchestrator_prompts_backend_first.py` | 增 | +1 项 | 提示词必须含 `todo_write`/`action=add` |
| `opc-agent-programming/tilacode/packages/core/src/plugin/agent.ts` | 修 | `BUILD_SYSTEM` 新增「Track multi-step work with the todo list」段 | 子 Agent 多步任务先调 `todowrite` 列计划并保持 in_progress（否则 `tasks.todos` 长期为空） |
| `opc-agent-programming/tilacode/packages/core/test/agent.test.ts` | 增 | +1 项「gives the default agent a todo list discipline」 | 待办纪律回归 |
| `opc-web-console/src/hooks/useTaskBuildingChat.ts` | 修 | `sendMessage` 的 `finally` | abort/断流后把 running 步骤标记 failed，不再永远「执行中」 |
| `opc-web-console/src/hooks/useTaskBuildingSessions.ts` | 修 | `refresh` | 列表拉取失败短退避重试 2 次（500/1500ms）后才暴露错误 |
| `opc-web-console/src/components/TaskSessionDrawer.tsx` | 修 | 新 `error` prop + `Alert`（重试按钮）+ 空态文案 | 网络失败不再伪装成「暂无历史会话」 |
| `opc-web-console/src/pages/TaskNew.tsx` | 修 | 新增 `handleSessionIdChange`（同步 `?session=`）、`?session=` 挂载恢复 effect（仅成功后置 ref，兼容 StrictMode）、`ensureSession` 改走同一入口、错误透传抽屉 | **切页/刷新后自动恢复当前会话**（会话 ID 此前只存在内存） |
| `opc-web-console/src/components/TaskSummary.tsx` | 修 | 上段新增「子 Agent 待办」标题 + 完成计数；空态改「子 Agent 暂未上报待办明细 / 子 Agent 未上报待办明细」 | 任务摘要明确上下两层（下段「中枢待办」不变） |
| `opc-web-console/src/pages/TaskNew.css` | 修 | `.task-summary__agent-todos(-head/-label)` | 上段标题样式（仅用 CSS 变量） |
| `opc-web-console/src/test/handlers.ts` | 增 | `GET /api/v1/tasks/build/sessions/:id` mock | 会话恢复测试数据 |
| `opc-web-console/src/__tests__/pages/TaskNew.test.tsx` | 增 | +1 项「URL 带 ?session= 时自动恢复」 | 回归 |
| `opc-web-console/src/__tests__/components/TaskSummary.test.tsx` | 修 | 上段标题/计数断言、空态新文案 | 回归 |

**验证**：中枢 `test_task_build_rounds.py` **3 passed**；`test_task_building.py` **9 passed / 1 failed**（失败为**既有**：`test_spec_command_round_persists_specification_and_steps` 与工作区未提交的 M4 `verify` 字段全等断言冲突，与本次改动无关）；提示词测试 **1 passed**；改动文件 `ruff` 全过。
节点 `bun test test/agent.test.ts` **11 passed**（含新增待办纪律项）；`bun typecheck` **exit 0**。前端 `TaskSummary.test.tsx` **9 passed**；TaskNew 新增 `?session=` 用例通过（该文件 3 项**既有**失败：引用源码已不存在的 placeholder/`parent-context`）；`tsc -b` 本次改动文件无错误；`eslint` 改动文件通过；颜色门禁无新增违规。
**生效前提**：节点提示词属 tilacode 二进制内容，需随 G1（重建二进制）/G2（重打模板）发布后才在生产节点生效；中枢与前端改动重启/构建即生效。

### 2026-10-02 · 规范体检修复批次（CI 门禁 / 类型完整性 / 组件拆分）

**背景**（用户 2026-10-02 追加要求）：按 `.trae/rules` 指引的 `docs/standards/*` 对全仓做规范性体检，并修复本次改动引入的违规项。

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `opc-web-console/src/components/TaskSummary.tsx` | 修（拆分） | 仅保留 `TaskSummary` 主体（329→125 行） | 拆出下辖区块后回到 200 行内，消除 4.3 组件超限与 CI 300 行门禁失败 |
| `opc-web-console/src/components/TaskSummaryTodoSections.tsx` | **增**（新文件） | `TodoItem` / `AgentTodoSection` / `OrchestratorTodoSection` | 上段子 Agent 待办 + 下段中枢待办（ADR-061 M5d），DOM/testid 与样式类完全不变 |
| `opc-web-console/src/components/TaskSummaryPanels.tsx` | **增**（新文件） | `STAGE_LABELS` / `CurrentAction` / `VerifyBlockedPanel` | 当前动作（ADR-055）与验收阻断决策（ADR-061 M4）区块独立成文件 |
| `opc-web-console/src/App.tsx` | 修 | 注释去掉 `#001529` 字面量 | 颜色字面量门禁（ADR-052）恢复通过 |
| `opc-web-console/src/components/dashboard/DashboardTaskTable.tsx`、`TaskTable.tsx`、`PlanReviewPanel.tsx` | 修 | 4 处 `Record<TaskStatus, string>` 补 `verify_blocked` 映射 | 消除 `tsc -b` 5 处类型错误（M4 新增状态未补标签/颜色） |
| `opc-central-dispatch-hub/tests/api/v1/test_task_building.py` | 修 | `_FAKE_SPEC` 补 `verify: None` | 会话持久化已含 M4 `verify` 字段，修正既有断言漂移（原该文件唯一失败） |
| `opc-central-dispatch-hub/app/services/resource_sync_service.py` | 修 | 改 `for item in cloud.values()`；合并 `async with` 上下文 | 消除 ruff B007 / SIM117 |
| `opc-central-dispatch-hub/app/services/terminal_service.py` | 修 | `contextlib.suppress` 替代 try/except/pass | 消除 ruff SIM105 |

**验证**：颜色门禁 `check_colors.py` 通过；`tsc -b` **exit 0**（原 5 处错误清零）；改动文件 `eslint` 通过；`TaskSummary.test.tsx` **9 passed**；中枢 `pytest`（test_task_building / test_task_build_rounds / orchestrator_prompts / resource_sync）**22 passed**；中枢 `app+tests` ruff **全过**；节点 `bun test test/agent.test.ts` **11 passed** + `bun typecheck` **exit 0**。
**扩展检查（同日追加：前端组件 / 后端组件 / 接口三规范）**：
- 接口规范（`api-standards.md`）：101 个操作中 91 个显式 `response_model`（其余 10 个为 204 / SSE / 文件下载豁免）；JSON 成功响应全部 `ApiResponse` 包装；创建类 POST 均 201；`TaskListResponse`/`SopListResponse` 等继承 `PaginatedResponse`（items+pagination）；WS 事件 `{模块}.{动作}` snake_case 与错误码实现均与规范表一致。
- 后端规范（`backend-component-standards.md`）：端点无直连 DB 写（无 `db.add/commit`）、无裸 `raise Exception`、无相对导入、无 `session.query()`、无 `orm_mode`；53 个 Alembic 迁移全部含 `downgrade()`；WS 消息均有 Schema（`app/schemas/ws_messages.py` 含 `TaskVerifyBlockedPayload`/`HUB_TASK_STEER`）；**本次修复**：`logs.py` 的 `GET /ring-buffer/stats` 补声明 `response_model=ApiResponse[dict[str, Any]]`。
- 前端规范（`frontend-component-standards.md`）：颜色字面量 / `any` 零违规（颜色门禁通过）；无废弃 `visible=`/`destroyOnClose`；`Spin tip` 4 处均为嵌套模式；`message.*` 全部经 `App.useApp()`（23 个文件，无静态导入）；`initialValues` 无内联新对象；`tsc -b` exit 0。
- **待评审的规范文档缺口（未改）**：§3 错误码表缺 206/207/208/209/210 域与 202012 共约 20 个已实现错误码；§4.2 事件表缺 `hub.task_steer`、`agent.task_verify_blocked`；ADR-055/059/061 的部分 REST 端点（steer / verify-decision / insight / orchestrator-todos / eligibility / dispatch-preference、`/terminal`、`/sops`、`/experience`、`/bt-mcp`）未登记进 api-standards；`GET /api/v1/health/` 返回裸 `HealthResponse` 且带尾斜杠（无仓内调用方，外部监控契约待确认）；DELETE 状态码 200/204 混用（5 个 200 vs 3 个 204）。

**存量违规（非本次引入，体检报告已列出）**：CI 行数门禁 64 个文件 >300 行（34 个未触碰、30 个在既有工作区改动中）；全仓 `eslint` 3 error/1 warning（TaskDetailModals / usePolling / Logs）；`ruff format --check` 48 个文件待格式化；`opc-central-dispatch-hub/scripts` 27 项 ruff（26 项可自动修）；Python 函数体 >50 行 133 个；`TaskNew.test.tsx` 3 项陈旧失败（引用已移除的 placeholder/`parent-context`）；api-standards §6 的 API 限流未实现（桌面单机形态风险有限，公网暴露前必须补）。

### 2026-10-02 · P0 事故修复：Agent WS 接收异常热循环（hub 假死 / 日志暴涨）

**发现过程**：扩展接口规范审查时，运行中的 hub（uvicorn :8000）HTTP 全部超时；进程 CPU 233s / 启动 5 分钟，
`logs/app.log` 404MB、`logs/error.log` 402MB 且以 ~1MB/s 持续增长（违反 `logging-standards`「单个文件不超过 100MB」）。

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `opc-central-dispatch-hub/app/api/v1/endpoints/agent_node_ws.py` | 修 | `agent_node_websocket` 接收循环：新增 `except json.JSONDecodeError`（告警后 `continue`）；通用 `except Exception` 由 `continue` 改为 `break` | **根因**：连接级 `RuntimeError: WebSocket is not connected`（Starlette）被 `continue` 吞掉且 `receive_json` 立即抛错不让出事件循环 → 死循环每秒刷大量 ERROR traceback，事件循环被打满（HTTP 全部超时）、日志暴涨；改为退出循环，由节点重连退避恢复 |
| `opc-central-dispatch-hub/tests/api/v1/test_agent_node_ws.py` | 增 | +1 项 `test_receive_error_exits_loop_instead_of_hot_spinning` | 回归：连接级异常必须只触发一次并退出（`wait_for` 超时即失败，避免挂死） |

**验证**：`ruff` 全过；`test_agent_node_ws.py` **3 passed / 1 xfailed / 1 xpassed**；重启后 hub 3s 内健康检查 200，
`error.log` 8 秒内零增长，任务 API 恢复（首页 1 assigned / 3 pending）。
**遗留**：日志轮转仅按天切割、无单文件大小上限（`app/core/logging.py`），热循环类故障仍可能单日写出超大文件，建议补 size cap。

## 明确不做（登记以便追溯）

| 事项 | 原因 |
|------|------|
| 任务级并行多 agent / 移植 V2 `task` 工具 | V2 子 agent 通道断开（`builtins.ts:26-29` 待移植）；ADR-055 已否决由中枢拆任务链承接并行 |
| 把 V1 `SessionRetry` 的无上限重试直接照搬 | 与「防白烧 token」矛盾，M3 必须加上限 |
| 恢复 `ask` 语义的「等人确认」流程 | 无人值守场景下 `ask` 是最差语义（既不安全又会挂死） |
| 用更聪明的模型替代机制建设 | A1/A4/A8 是结构问题 |

## 登记记录

- 2026-10-02：**P0 事故修复**（详见上方同名小节）——接口规范审查时发现 hub 假死：`agent_node_ws` 接收循环把连接级 `RuntimeError` 当消息级错误 `continue`，形成不让出事件循环的热循环，5 分钟写出 400MB+ 日志并打满事件循环。修复为「JSON 解码错误告警后继续、其余接收异常记录一次并退出」+ 回归用例；已重启 hub 恢复（3s 健康检查通过、日志零增长）。遗留：日志单文件大小上限待补。
- 2026-10-02：**规范体检修复批次**（详见上方同名小节）——修复本次改动引入的合规问题：`TaskSummary` 组件拆分（329→125 行）、颜色字面量门禁、5 处 `verify_blocked` 类型缺失、ruff 3 项；顺带修正 `test_task_building.py` 的 `_FAKE_SPEC` 断言漂移并补 `logs.py` `/ring-buffer/stats` 的 `response_model`。验证：前端 tsc exit 0 + 目标用例通过、中枢 22 passed + ruff 全过、节点 11 passed。同日扩展检查前端/后端/接口三规范，发现的规范文档缺口（错误码表/事件表/端点登记、health 响应、DELETE 状态码、限流）已在上方小节登记，存量违规清单随体检报告输出，非本次引入。
- 2026-10-02：**用户三问题修复批次**（详见上方同名小节）——①步骤卡卡「执行中」根因 = 回合分割后 `step_finished`
  只查当前回合、流中断又无收尾；②历史会话 = 抽屉不显示错误且无重试、会话 ID 未入 URL；③待办 = 节点 `BUILD_SYSTEM`
  与中枢 base prompt 均无 todo 纪律。修复后：中枢新增用例全过（既有 1 项失败与 M4 `verify` 字段全等断言冲突，非本次引入）、
  节点 11 passed + `bun typecheck` exit 0、前端目标用例通过（TaskNew 3 项既有陈旧失败已登记）。
- 2026-09-29：**收尾一批**（ADR-061 收尾）：① **G4 补改**——`tilacode/packages/core/src/tool/skill.ts` 的错误映射由
  「一律 `unableToLoad`」改为 `Tool.permissionFailure(error, fallback)`（权限拒绝保持**可读**，不再让模型反复重试被拒路径）；
  `bun typecheck` 在 `packages/core` **exit 0**。② 补 **M5a-steer 缺口**：节点**桥接级** 3 项（`TaskBridge.steer` 的会话归属
  `ses_task_{项目键}`、**已知进度回传**送达确认、不打断任务、链上落到根会话）+ 中枢 **API 级** 1 项
  （`POST /tasks/{id}/steer` 的 **200/409/422/404** 契约）。③ Harness 回填 `UT-M7-*` 与 `UT-M6-*` 状态、M6/M7 阶段门槛行。
- 2026-09-29：**跨模块回归核对（收尾）**——节点全量 **223 passed（exit 0）**（含需放宽沙箱的 3 项真实子进程用例）；
  中枢跨模块集合（事件处理/调度器/steering/预算/洞察/经验 M6 两文件/判据/验收阻断/中枢待办技能）**103 passed**，
  期间暴露并修正 **1 处用例期望**：M5c 的动作留痕会让进度用例多出一次 `tool_call_log_service.record` 调用，
  该用例改为**按 `kind` 精确定位 `llm_usage` 行**（保持原意图），修正后该文件 **41 passed**。
  前端相关用例此前已绿（`TaskSummary` 9 + `TaskInsightPanel` 6）。

- 2026-09-28：**创建骨架**。基于当日 4 份只读审计（tilacode V2 / 节点 / 中枢 / 前端）立项 ADR-061，
  固化 M1–M7 模块拆分、依赖顺序与「一次只做一个模块」的推进方式；Spec/Harness 同步完成，**未写生产代码**。
- 2026-09-28：**记录用户已定边界**——① 全权开放权限（不引入等人授权）；② 经验记忆先做节点侧、后续打通中枢跨机复用；
  ③ 中枢要把目标节点会话拉进来、默认折叠可展开；④ 本地有 bun，Linux 二进制可编（流程已记入 Harness §6）；
  ⑤ 源码级 E2E **已授权但暂不执行**；⑥ 另有并发会话在跑，须按 ADR-061 D3.4 划界。
- 2026-09-28：**更正历史结论**——项目网络未被封（npm/GitHub 实测 200），此前「不可达」为 PowerShell schannel 假象；
  该更正同时解锁 Playwright 浏览器安装（M7）与 Bun 交叉编译（B1）。
- 2026-09-28：**遗留待办**——ADR-060 的 P4（摘要热前缀）源码级 E2E 尚未执行，与 E2E-A5 共用同一次授权。
- 2026-09-28：**并发约束解除**——用户确认另一会话已停止，ADR-061 D3.4 的原受限区域（中枢 / 前端）解禁；
  文档固化阶段结束，**用户选择先自行阅读文档，暂不开工**（无代码改动）。
- 2026-09-28：**用户确认 6 项决策（ADR-061 D5.1~D5.6）并同步进文档**：
  ①权限策略**由中枢统一下发**、工作区内 allow/**工作区外一律 deny**（含 `.env`），例外只能走显式白名单；
  ②完成闸**从严**（用户"比较怕假成功"）——「无法验证」也不得呈现为正常完成，不允许"无验证结论却 completed"的路径；
  ③会话获取**选定只读镜像**并要求**实时**可见"正在执行哪条命令"，同时新增**中枢↔节点直接对话**（`@节点A` → steer → 回复经镜像回流）；
  ④参数：自修 **≤5 轮**、预算软阈值 **80%**（硬 100% 保留）；
  ⑤**任务摘要分两层**（上层=子 Agent 待办、下层=中枢待完成任务）且实时更新；
  ⑥澄清通道口径——节点链路 = **V2**，V1 为保留旧路径（Spec 新增 §1.1 对照表）。
  同步更新：Spec §2/§3-M2/M4/M5/§5、Harness M1b 勘察线索与 M2/M4/M5/§5/§7、ADR-061 D2/D5 与用户诉求表。
- 2026-09-28：**截图带来的两条线索**（用户提供 TUI 截图）：
  ①状态栏 **`LSPs are disabled`** → 已登记为 M1b 前置勘察的第一手线索（V2 侧更可能"存在但被禁用/未接线"，
  而非"完全没有 LSP 代码"），据此决定是接线还是移植；
  ②状态栏 `Context 122,989 tokens / 61% used` 反推**该会话上下文窗口约 200K（而非 1M）**——
  若这是节点任务会话，则**节点侧压缩会经常发生**，ADR-060 P4（摘要热前缀）的价值高于原先估计；
  **待核实**：该会话所用模型与其窗口配置（节点侧模型目录 vs ADR-056 只改了中枢侧 `context_window`）。
  ③状态栏 `$0.00 spent` 查证为**结构性 0**——V2 runner 发布 `Step.Ended` 时 `cost` **硬编码为 `0`**
  （`packages/core/src/session/runner/llm.ts:331`，全文件仅此一处 `cost`），故节点上报的 `detail.cost` 恒为 0.0。
  **影响**：M5b 预算闸**不能**建立在花费维度上（除非另补价格计算），默认改用**步数 / token**维度。
- 2026-09-28：**M1a 实施完成并验证**。改动 1 个生产文件（`packages/core/src/plugin/agent.ts` 的 `BUILD_SYSTEM` 常量）
  + 1 个测试文件（`test/agent.test.ts` 增 `builtInAgents` 助手与 3 项用例）。第一句人格定义原样保留，控制流与装配零改动。
  验证：`test/agent.test.ts` 10/10 通过；`bun typecheck` 退出码 0；`packages/core` 全量 986 通过 / 7 跳过 / 100 失败
  （失败集合与改动前一致，多出的 1 项为 `Npm.add` 在全量并行下的 5s 超时，**单独跑 998ms 通过**，非回归）。
  效果属提示词层面的行为倾向，**需重建 tilacode 二进制后才能观察**（口径：ADR-060 基线命中率 95.2% 不劣化 + 步数/时长无膨胀）。
  同时按用户 2026-09-28 要求，把「分阶段汇报四段格式」写进 ADR-061 D3.5（完成内容 / 实现效果 / 剩余未完成 / 正在开始）。
- 2026-09-28：**M2a 实施完成并验证**（无人值守权限语义 + 拒绝原因回灌 + 节点移除 blanket allow）。
  tilacode 侧 7 个文件（V1/V2 schema + 迁移 + `permission.ts` + `tool/tool.ts` + 11 个工具的错误映射）、
  节点侧 1 个文件（模板 + 存量自愈）、测试侧 6 个文件（新增 3 用例 + 更新 10 处断言）。
  验证：`bun typecheck` 退出码 0；`packages/core` **990 通过 / 7 跳过 / 99 失败**（99 项全为既有沙箱环境类，
  **非环境类失败为空**）；节点 `python -m pytest tests -q` **182 通过**；`ruff check app tests` 全过。
  **效果**：① 工作区外访问与 `.env` 读取在无人值守下**一律被拒**（此前被 blanket allow 放行）；
  ② `ask` 不再无限等待 → **挂起隐患消除**；③ 拒绝原因带命中规则与"不要重试"提示回灌给模型。
  **部署前提**：tilacode 需重建二进制；节点需重新打包模板（已授权，待用户指定时机）。
  **遗留**：`tool/skill.ts` 的错误映射未改造（未核实）；M2b 未开始。
- 2026-09-28：**M2 勘察完成（未动代码）**，两条假设被推翻并记入 ADR-061「M2 · 实施范围」：
  ① **节点写的 `"permission": {"*": "allow"}` 会覆盖 tilacode 全部内置护栏**——迁移为 `{*:*, allow}`
  （`v1/config/migrate.ts:74-91`）后由配置插件**追加到权限数组末尾**（`config/plugin/agent.ts:77`），
  而求值是 `findLast` 最后匹配者胜（`permission.ts:76-86`）→ `.env` 读取 ask、工作区外 ask、`question/plan_*` deny、
  tool-output/tmp/plans 白名单**全部失效**（当前部署比内置默认更开放）；
  ② **"工作区外一律 deny"不能写在节点配置里**——节点不知道 tilacode 内部白名单路径，而后追加的 deny 会把它们一起 deny 掉。
  另查实：**拒绝原因到不了模型**——各工具 `mapError(() => new ToolFailure({...}))` 把 `BlockedError` 塌缩成
  `Unable to execute command: X`（`bash.ts:196`），模型会反复重试同一条被策略拦截的路。
  **据此建议 M2 拆 M2a（无人值守 ask→deny + 原因回灌 + 节点移除 blanket allow）/ M2b（中枢下发策略）**，待用户确认。
- 2026-09-28：**M3 勘察完成 + 实施范围登记 + 三项设计分叉已确认**（未动代码）。勘察的两处关键发现：
  ① **V1 的 Doom Loop 判定范围是"同一条 assistant 消息内最近 3 个工具调用"**（`opencode/src/session/processor.ts:29,356-380`），
  而 **V2 每个 provider turn 通常只发一个调用** → 照搬会让窗口永远凑不满 3、**永不触发**，必须改为**跨 step** 统计；
  ② **V1 重试无上限次数**（`retry.ts:176-199` 仅对不可重试错误停止），与"防白烧 token"矛盾，
  且传输层已有 2 次重试（`llm/src/route/executor.ts:36-38`）→ 会话级重试必须**加上限**（确认：最多 5 次 / 总时长 60s）。
  另确认 `delay()` 语义可安全移植（优先 `retry-after-ms`/`retry-after` 头，否则 `2000×2^(n-1)` 封顶 30s）。
  **用户确认**：打转命中 → **跳过该调用 + 注入提示让模型换做法**（不失败、不强制结束回合）；重试上限 **5 次 / 60s**；
  无进展信号**纳入本模块但只记信号**（不做自动停止）。下一步：M3 代码实现。
- 2026-09-28：**M3 实施完成并验证**（防打转 + 有上限会话级重试 + 无进展信号）。新增 2 个源文件
  （`session/retry.ts`、`session/doom-loop.ts`）+ 改 1 个源文件（`runner/llm.ts` 四处）+ 新增 2 个测试文件 + 1 个测试文件加 2 项。
  验证：`bun typecheck` 退出码 0；`packages/core` **1003 通过 / 7 跳过 / 99 失败**（99 项全为既有沙箱环境类，
  **非环境类失败为空**；较 M2a 的 990 通过新增 13 项全为本次用例）。**效果**：① 连续 3 次同工具同参的调用
  **不再被执行**，模型收到带说明的失败结果并继续（不再空转烧 token）；② 瞬时 provider 错误在 **5 次 / 60s** 预算内
  自动重试，用尽才失败（V1 的无上限重试被有意收窄）；③ 连续 4 步无文件变更时记可观测信号。
  **部署前提**：tilacode 需重建二进制才在生产生效。
- 2026-09-28：**重建二进制 + 验证积压的 3 个模块**（用户选定"先重建验证"）。结果：
  ① **构建成功**：`linux-x64-baseline` 产物 140.7MB / sha256 `6041b5bb…` / version `1.18.13`（channel `local`）；
  ② **改动确认进入产物**：用字符串特征比对，M1a（2 串）、M2a（2 串）、M3（3 串）共 **7 个标记全部 FOUND**；
  ③ **节点侧 M2a 端到端验证 PASS**：新工作区模板无 blanket allow 且 `permission_mode=unattended`、
  存量工作区自愈迁移正确、迁移后 `provider` 段与 apiKey 完好；
  ④ **查清一个发布隐患并记入 Harness §6**：`InstallationChannel` 是构建期常量且节点不设 `TILACODE_DB`，
  故 DB 文件名由 channel 决定；已发布 manifest 为 `1.18.13` 而节点库是 `tilacode-local.db` → 必须
  **同时设 `OPENCODE_CHANNEL=local` 与 `OPENCODE_VERSION=<版本>`**；只给版本号会让 channel 变 `latest`
  → DB 变 `tilacode.db` → **节点以空库启动、丢会话历史**（实测构建脚本自打印 `{"channel":"local","version":"1.18.13"}` 佐证）；
  ⑤ **运行时 M2a 探针未成功**：先被沙箱拦（tilacode 需写工作区外的 `~/.local/share/tilacode/log/`，升级权限后通过），
  随后撞到 tilacode 内部 `UnknownError`（隔离临时库 + 模型解析链路的组合问题）。故"真实模型下工作区外被拒"
  **仍未被端到端证明**（单元/集成测试已覆盖，真实链路待补）；
  ⑥ **未发布**：产物仅存于 `dist/`，未复制到 hub static、未更新 manifest 的 sha256/size、未改安装脚本的
  `TILACODE_VERSION`（**有意不发布**——发布等于所有节点可升级，版本号需用户决定）。
- 2026-09-28：**用户决定：暂不发布、运行时探针不跑**（接受"单元/集成测试 + 产物特征比对"作为本轮验证口径）；
  产物留在 `dist/`，等后续模块一起发版。
- 2026-09-28：**M1b 前置勘察完成（未动代码），结论推翻原假设**——V1 有**完整** LSP 运行时
  （`opencode/src/lsp/` 6 个文件、17 处引用），而 **V2（`core/src`）只剩配置 schema、没有任何 LSP 运行时**；
  截图里的 `LSPs are disabled` 出自 TUI 侧栏（`tui/src/feature-plugins/sidebar/lsp.tsx:25`），
  在 V2 下没有可上报"已启用"的服务所以恒为 disabled。**故 M1b 不是"接线"而是"从 V1 移植"**，
  规模大于原估；已按**推迟**登记（不阻塞 M4），并列出三个备选方案待用户决定。
- 2026-09-28：**M4 勘察 + 范围登记；用户否决"复用 review 静默等待"，提出更优方案**（未动代码）。
  勘察要点：`TaskSpecification{spec,tasks,checklist}` 是三段自由文本无结构化判据（`schemas/task_building.py:11-16`）；
  完成判定纯静默（`sse_consumer.py:378-385`）且 `_dispatch_result` 不读 `specification`（`task_bridge.py:879`）；
  节点**无任何测试/lint/build 执行能力**（唯一子进程是 tilacode 自身）。**关键澄清**：`review` **不是**"无法验证"状态，
  它是既有的「方案待人工确认」（ADR-038），且属 `ACTIVE_STATUSES`（非终态）＋超时兜底**只覆盖 `running`**
  （`scheduler.py:648,698,711`）→ **单纯复用它会一直等人**（不静默：仪表盘有告警、有操作入口，但没人点就永不结束）。
  **用户方案（采纳）**：不静默停状态，改为**中枢在该任务对话区主动告知用户、由用户决定**，走 steer 通道；
  **并加 24h 超时兜底**（无回应 → 自动 `failed` 并写明原因）。**据此调整顺序**：先做 **M5a-steer**（小通道，
  是"M4 主动找人"+ "M4④ 自修" + "M5b 软阈值收尾"的共同前置），再做 **M4 全量**。
  已确认：必过层 = L0/L1 必过、L2 存在则必过。
- 2026-09-28：**M5a-steer 实施完成并验证**（中枢↔节点定向发言通道）。中枢 4 个文件（事件常量 + `steer_task` +
  请求模型 + `POST /{task_id}/steer`）+ 1 个新测试文件（6 项）；节点 2 个文件（事件常量/分发/`_handle_task_steer` +
  `TaskBridge.steer` 与 `_last_progress_value` 跟踪）+ 1 个测试文件加 5 项。
  **验证**：中枢 steer 6/6 通过（cancel 无回归）；节点该文件 22/22、节点全量 **187 通过**；本次涉及文件 ruff 全过。
  **关键语义**：steer **不改变任务状态**（与 cancel 的本质区别，已在测试中断言 `status` 与 `progress` 均不变）；
  节点离线时中枢**显式报 409** 而非静默丢弃；节点侧送话失败不外抛（任务照常继续）。
  **旧节点兼容**已核实：未知事件落到 `unhandled_event` 调试日志，无白名单拦截。
  **缺口**：桥接级与 API 级用例未写（服务级与 handler 级已覆盖）。
- 2026-09-28：**任务摘要口径二次澄清（重要修正）**——用户追问"现在任务摘要显示哪些内容"，遂逐行读
  `opc-web-console/src/components/TaskSummary.tsx`（178 行）确认现状为 **6 区块**：标题 / 状态 Tag + 任务标题 /
  进度条 / **当前动作**（stage Tag + 文案）/ 子 Agent 待办列表 / 空态；且**全部数据来自节点上报**
  （`task.status/title/progress/progress_stage/progress_message/todos`），中枢侧不产生任何一块。
  **据此修正**：① 上段 = **现有 5 块全留**（早前写成"上层=待办"过窄）；② 下段 = **中枢编排器自己的待办**
  （早前写成"中枢规划任务 `tasks.md` + 执行待办两个子区"或"由任务链派生"，**均系推测，已废弃**）；
  ③ 新增独立模块 **M5d**（中枢待办：已核实中枢 `tasks.todos` 只存节点上报内容、技能注册表约 20 个技能无 todo 能力
  → 真新增，含存储 + 编排器 todo 技能 + 摘要下段渲染；依赖无，可独立实施）。
  同步更新：Spec §2-US-12/§3-M5c/§3-M5d/§5-M5/§5-M5d、ADR D5.5/B9/D2 路线图、Harness UT-M5-7/UT-M5-10/11/E2E-A7/A8/§7。
  另核实两条现状限制并记入：① 中枢丢弃 `tool` 字段，命令是节点把 bash 主参数**拼成的一句话**且**截断 60 字**
  （`event_translator.py:403-420,51-71`），无耗时/退出码；② 进度条是**事件计数**推导（10→20→每事件 +2 封顶 90→完成 100，
  与 todo 完成比取 max），**不是真实完成度**。
- 2026-09-28：**M5a 用量落库 + 字段不再丢弃 实施完成并验证**。① `tool_call_logs` 新增 `llm_usage` kind（模型 + CHECK + 迁移
  `20260928c_llm_usage_kind`）：`_record_cache_metrics` 把 ADR-060 P0 的日志口径**同源**落库（`input/output/reasoning/cache_read/
  cache_write/cache_hit_rate/cost/files_touched`），仍是旁路（独立会话 + 兜底，写失败不回滚进度）；② 新增 `tasks.progress_tool`
  （迁移 `20260928d_task_progress_tool`）并修掉上一条记录中的限制 ① 的**中枢侧丢弃**部分 —— `tool` 现在落库且可经 API 读取
  （节点侧"命令是一句话、截断 60 字、无耗时/退出码"的限制**仍在**，未动）。
  **验证**：handler 测试 41 passed、关联服务测试 20 passed、ruff 全过；两迁移在库生效（`alembic_version` + `information_schema`
  + `pg_constraint` 三处实证）；真实落库探针插入并清理成功。**测试库无需手工迁移**：测试 fixture 会自动 `upgrade head`。
  **顺带勘误**：alembic 命名约定会给字面约束名再加前缀，DB 实际名为 `chk_tool_call_logs_chk_tool_call_logs_kind`（既有迁移同理），
  故守卫测试按 `sqltext` 内容而非名字定位约束。
  **下一步**：M2b 中枢下发权限策略（S）。
- 2026-09-28：**M2b 实施完成并验证**（中枢下发权限策略）。用户裁决 Q1~Q4 **全部取推荐方案**：
  ①全局单条策略（`system_settings.permission_policy`）；②节点本地缓存 + 派发即权威、无策略按最严；
  ③非白名单询问回 `reject` + 可读原因；④中枢记下发（并入 dispatch 审计）+ 节点记每次判定。
  **实施**：中枢 6 文件（模型列 + 迁移 `20260928e` + 新服务 + schema + GET/PUT 端点 + 两处派发点），
  节点 5 文件（新模块 `permission_policy.py` + payload 字段 + `ensure_tilacode_config` 应用策略 + `task_bridge` 策略驱动判定 + `permission_reply(message=)`）。
  **修掉的实测放行口**：节点原对**每个**权限询问无条件回 `once`（`tilacode_client.permission_reply` 默认实参），
  等于把「工作区外 deny / `.env` 拒绝」等护栏全部绕过。
  **验证**：中枢 `test_settings.py` 11 passed（+4）、`test_task_lifecycle.py` 4 passed（含 WS 端到端策略断言）；
  节点 **200 passed**（+13）；两侧 ruff 全过；迁移在库生效。
  **登记的环境噪声**：库中上轮中断测试残留的 `test-task-*` pending 任务会使 `test_task_lifecycle` 收到**别的任务**的派发而误报失败
  （与本次改动无关，已清理该模式残留后复跑通过；真实用户 pending 任务未动）。
  **已知缺口**：管理台策略编辑 UI（归 M5c）、节点模板 tarball 未重打（G2）、任务级 override 未做（Q1 选全局单条）。
  **下一步**：M5d 中枢编排器待办（M）。
- 2026-09-28：**M5d 实施完成并验证**（中枢编排器待办）。用户裁决 **Q5 = 只读展示**（前端不提供勾选/新增/删除入口；`source` 预留 `user`）。
  **实施**：中枢 8 文件（新表 + 迁移 `20260928f` + 新服务 + 新技能 `todo_write` + 注册 + schema + 只读端点 + 测试），
  前端 7 文件（类型 + API + 轮询钩子同轮取数 + `TaskSummary` 下段 + 面板/页面透传 + CSS + 测试）。
  **与节点待办物理分离**已由专门用例守卫（写入中枢待办后 `tasks.todos` 不变）。
  **验证**：中枢技能 8 passed、端点 2 passed、ruff 全过；前端 `TaskSummary` 7 passed；迁移在库生效。
  **登记基线（非本次引入）**：前端全量 `vitest` 71 passed / **5 failed（3 文件）**，失败原因已逐条定位为「测试期望的 testid/placeholder 已不在组件中」
  与「另一工作流正在修改 `TaskDetailDrawer.tsx`」；`tsc -b` 另有 2 处同类既有类型错误。**本次改动文件无新增失败**。
  **已知缺口**：下段只读（Q5）、实时性依赖既有 5 秒轮询、待办无历史留痕。
  **下一步**：M4 全量（L）。
