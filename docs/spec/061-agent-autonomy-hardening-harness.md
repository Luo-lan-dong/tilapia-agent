# 子 Agent 全自主开发能力加固 Harness（M1–M7）

> 关联：[ADR-061](../adr/061-agent-autonomy-hardening.md) · [Spec](./061-agent-autonomy-hardening-spec.md) · [Changes](./061-agent-autonomy-hardening-changes.md)
> 测试编号按模块前缀（`UT-M1a-*`）命名，避免与既有清单（ADR-060 的 UT-86~UT-106 / IT-18~21 / E2E-6~7）冲突。

## 1. 测试金字塔

```
      E2E（4）      验收红不报完成 / 超预算先软后硬 / 会话镜像可见 / 视觉反馈可定位
       集成（5）      权限兜底不挂 / 重试与打转拦截 / verify 分层执行 / steer 贯通 / 经验跨机召回
       单元（20+）     提示词断言 / 权限语义 / doom loop / 重试策略 / 分层探测 / verify 解析 / LSP 回灌
```

## 2. 单元测试清单

### M1a 干活纪律（tilacode `packages/core`）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-M1a-1 ✅ | 纪律要点存在 | 默认 agent 的 `system` 含 6 条关键短语（自检 lint/typecheck/测试、不臆测命令与测试框架、失败读原文改因后重跑、不问"要不要跑"、无检查须明说未验证、收尾报告三要素） |
| UT-M1a-2 ✅ | 纪律不串味 + 无控制流副作用 | `explore`/`compaction` **不含**该纪律；`plan` 仍无 `system`；`runner/llm.ts` 两段式装配未变（守 ADR-060 前缀不变量） |
| UT-M1a-3 ✅ | 既有提示词未被误改 | `explore`/`compaction` 的既有特征串仍在（本次只应影响 `BUILD_SYSTEM`） |

> **M1a 实施记录（2026-09-28）**：3 项用例落在 `packages/core/test/agent.test.ts`，该文件 **10 项全过**；
> `bun typecheck` 退出码 0；`packages/core` 全量 986 通过 / 7 跳过 / 100 失败（失败集合与改动前一致，
> 多出的 1 项 `Npm.add` 为全量并行下的 5s 超时，单独运行 998ms 通过）。

### M2 权限语义收敛

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-M2-1 ✅ | **开关生效** | `permission_mode="unattended"` 下 `ask` 规则 → `BlockedError`（`assert`）且 `ask` 返回 `deny`；**不排队、不阻塞** |
| UT-M2-2 ✅ | 工作区外默认 deny | 无人值守下 `external_directory` 类请求被拒（工具拒绝文案含策略原因）；`interactive` 下行为与改动前一致 |
| UT-M2-3 ✅ | 敏感文件 deny | `.env` 读取在无人值守下被拒 |
| UT-M2-4 ✅ | 白名单例外 | 内置 tool-output/tmp/plans 白名单**仍可用**（既有用例 "allows managed output reads without granting external directory access" 通过） |
| UT-M2-5 ✅ | 拒绝原因回灌 | `BlockedError` → `ToolFailure` 文案含 `Blocked by permission policy` + 命中规则 + `retrying it fails the same way` |
| UT-M2-6 ✅（M2b，口径修正） | 兜底拉取 | 用户裁决 Q2 选了「**本地缓存 + 派发即权威、无策略按最严**」，**不新增事件对**：节点内存无策略记录时**回读工作区配置**（配置文件即缓存），两者都无 → 最严。M2a 后无人值守下不再产生 pending，故无需定期拉取 |
| UT-M2-7 ✅（M2b） | 审计痕迹 | 中枢：`dispatch` 审计行含 `permission_policy_mode/allow_count/allow`；节点：每次判定记 `permission_auto_replied` / `permission_auto_rejected`（含 action/resources/mode/allow_count/evaluable） |

**M2b 权限策略下发（2026-09-28 已完成）**

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-M2b-1 ✅ | 归一化 | `None`→沿用缓存；非 dict / 非法 mode → 最严；**blanket allow（`*`/`*`）被丢弃**；非法条目逐条剔除；条数/长度上限 |
| UT-M2b-2 ✅ | 工作区落地 | 写入 `permission_mode` + allow 条目；三种历史 blanket allow 形式被清除；provider 段保留；无策略 → 沿用缓存 |
| UT-M2b-3 ✅ | 回读生效策略 | `effective_policy_from_config` 与归一化策略一致（判定口径来源） |
| UT-M2b-4 ✅ | 判定镜像 | `wildcard_match` 镜像 tilacode（`\`→`/`、`*`/`?`、Windows 忽略大小写、`"git *"` 匹配裸 `git`）；无白名单一律 False |
| UT-M2b-5 ✅ | 命中白名单 | `permission.v2.asked` 命中 → `reply="once"` 且不带原因 |
| UT-M2b-6 ✅ | 未命中 | → `reply="reject"` + 可读原因（含动作/资源与「不要重复尝试」指引） |
| UT-M2b-7 ✅ | fail-closed | `action`/`resources` 缺失或不可评估 → 一律拒绝（宁可多拒不可少拒） |
| UT-M2b-8 ✅ | 拒绝原因可读 | `deny_reason` 含动作、资源、白名单指引 |
| UT-M2b-9~12 ✅ | 中枢 API | 默认最严 / PUT-GET 一致 / blanket 被服务层丢弃 / 额外字段 422 |
| IT-A1（扩展）✅ | 派发携带策略 | WS 端到端：`hub.task_dispatch.payload.permission_policy` 存在且 `mode` 合法、`allow` 为列表（自动派发与手动派发两处均挂载） |
| UT-M2b-13 ⬜ | 管理台编辑 UI | 归 M5c/前端；本轮只有 API |

> **M2a 实施记录（2026-09-28）**：`packages/core/test/permission.test.ts` 新增无人值守 harness + 2 用例 +
> 1 项 `permissionFailure` 文案用例；`tool-{read,edit,write,todowrite,question}.test.ts` 6 处拒绝文案断言更新。
> `bun typecheck` 0；`packages/core` 990 通过 / 7 跳过 / 99 失败（**非环境类失败为空**）；
> 节点 `pytest` 182 通过 + ruff 全过。**节点侧断言**（配置不含 blanket allow、含 `permission_mode`）落在
> `opc-agent-programming/tests/test_workspace.py`（4 处更新）。

### M3 防打转与会话级重试

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-M3-1 ✅ | 打转拦截（跨 step） | 同工具 + 字节相同入参连续 3 次 → 该回合**不执行**、模型收到含 `Detected the same tool call repeated` 的失败结果、运行继续（集成用例：`executions` 仅 2 次 + 共 4 轮请求） |
| UT-M3-2 ✅ | 参数不同不误伤 | 入参递增时**不**触发（3 次全执行）；不同工具、不同 session 互不影响 |
| UT-M3-3 ✅ | 重试上限 | `attempt >= MAX_ATTEMPTS(5)` → `attempts-exhausted`；`elapsedMs + delay > 60s` → `budget-exhausted`；可重试时推进 attempt |
| UT-M3-4 ✅ | 不可重试错误 | `retryable=false` → 不重试；`ContextOverflow` 在 runner 侧被显式排除（走既有压缩路径） |
| UT-M3-5 ✅ | 退避延迟 | provider 显式延迟优先且封顶 30s；否则 `2000×2^(n-1)`；非法值忽略 |
| UT-M3-6 ✅（信号） | 无进展信号 | 连续 4 步无文件变更 → `Effect.logWarning("session steps produced no file changes")`；**不改行为**（日志级，未写断言） |

> **M3 实施记录（2026-09-28）**：新增 `test/session-retry.test.ts`（7 项）与 `test/session-doom-loop.test.ts`（4 项），
> `test/session-runner.test.ts` 加 2 项集成用例。`bun typecheck` 0；`packages/core` **1003 通过 / 7 跳过 / 99 失败**
> （**非环境类失败为空**）。**已知缺口**：runner 级"重试后成功"用例受假客户端限制未写（`streamFailure` 对每次
> `stream()` 都生效），由纯函数用例覆盖。

### M1b 编辑后 LSP 回灌

> **前置勘察的第一手线索（2026-09-28 用户提供截图）**：本机 TUI 状态栏显示 **`LSPs are disabled`**。
> 这说明 V2 侧不是"完全没有 LSP 代码"，更可能是**存在但被禁用/未接线**——勘察应优先确认这一点，
> 再决定是"接线即可"还是"需从 V1 移植服务"（后者则单独立项）。

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-M1b-1 | 诊断回灌 | 编辑引入语法错误 → 工具输出含诊断文本 |
| UT-M1b-2 | 无诊断静默 | 无诊断时不追加任何文本 |
| UT-M1b-3 | 降级 | LSP 不可用/超时 → 编辑仍成功（不得失败） |

### M4 可执行验收

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-M4-1 ✅ | `verify.json` 生成 | 判据轮提示词产出结构化 JSON；**解析容错**（去围栏/取首尾括号）；解析失败 → `None`（不丢三件套） |
| UT-M4-2 ✅ | 判据来源优先级 | `verify.json` > 项目自带配置（package.json scripts / pyproject / Makefile）> 按文件类型推导 > **无（无法验证）**；必过层规则：L0/L1 必过、L2 存在即必过、L3/L4 需显式 |
| UT-M4-3 ✅ | 分层顺序与超时 | L0→L4 顺序执行；默认超时 30s/60s/5min/10min/20min（单条可覆盖并受上限约束）；**超时按未通过计** |
| UT-M4-4 ✅ | 无可用检查 | 无判据 → `unverifiable`（显式「无法验证」），**不得**静默判成功；闸门对此**不自修、直接阻断** |
| UT-M4-5 ✅ | 完成闸 | 必过层失败 → 不放行（`_run_completion_gate` 返回 None，不触发 `on_completed`）；非必过失败不阻断 |
| UT-M4-6 ✅ | 自修回合预算 | 失败回灌含**失败命令 + 输出尾部**；**≤5 轮**；用尽 → 阻断上报（`repair_rounds=5`） |
| UT-M4-7 ✅ | 失败判据落痕 | 报告 `to_payload()` 含层/命令/必过/退出码/耗时/输出尾部；中枢落 `tasks.verify_report` + `tool_call_logs` 审计 |
| UT-M4-8 ✅ | 阻断处理器 | 置 `verify_blocked` + 落报告/阻断时刻 + **在构建会话追加「等你决定」消息**（含两个选项）+ 审计；非执行态忽略 |
| UT-M4-9 ✅ | 24h 兜底 | 阻断超 24h 无人决定 → `failed` 并写明原因；未超时不动 |
| UT-M4-10 ✅ | 用户决策 | `accept` → completed 并记录「用户接受现状」；`retry` → 回 running 并把失败摘要 steer 给节点；非阻断态 409 |
| E2E-A1 ⬜ | 真实链路 | 需真实节点任务（并入源码级 E2E 时机） |
| UT-M4-8 | **守卫：不得假成功** | 不存在"无验证结论却 completed"的路径；「无法验证」产出独立状态 + 摘要显著标记；自修轮数上限为 5 |

### M5 预算化与可观测

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-M5-1 | 用量落库 | 每个上报统计点一行 `llm_usage`，含 in/out/reasoning/cache_read/cache_write/hit_rate/cost/files_touched/tool ✅ 已实现（`UT-M5a-3`） |
| UT-M5-2 | 命中率计算 | `cache_read/(input+cache_read)`；零 token 不除零（与 ADR-060 口径一致）✅ 已实现（`UT-M5a-3/4`） |
| UT-M5-3 | 预算阈值 | 80% 触发 steer、100% 触发 cancel；阈值可配 ✅ 已实现（`test_budget_gate.py` 7 项，含**幂等**与**软通知失败下轮重试**） |
| UT-M5-4 | 字段不再丢弃 | `tool` → `tasks.progress_tool` 落库且 API 可读 ✅；`detail` → **以 `llm_usage` 行落库**（不新增 tasks JSON 列）✅；节点 `session_id` **仍按既有设计只进日志**（`ws_messages.py` 注释：不写 `tasks.session_id`），用量行写入时**不猜测**会话名 ⬜ 保持原设计 |
| UT-M5-5 | 告警规则 | 命中率跌破基线 / 连续同签名调用 / 长时间无文件写入 各自触发 ✅ **后端已实现**（`test_task_insight.py` 6 项，含**样本不足不误报**）；前端展示归 M5c-2 |
| UT-M5-6 | 旧节点兼容 | 未知事件被忽略；缺字段不报错（`tool`/`detail` 缺失仅跳过，进度照常落库）✅ 已实现（`UT-M5a-4/7`） |
| UT-M5-7 | **上段保持现状** | 任务摘要上段仍渲染 5 块：状态 Tag、标题、进度条、当前动作、子 Agent 待办；不因新增下段而改动或串位 ✅ 已由 M5d 用例守卫（M5c-2 追加前端视图时继续遵守） |
| UT-M5-10 ✅（M5d） | **M5d 中枢待办存储** | 中枢可创建/推进/完成自己的待办并持久化；与 `tasks.todos`（节点上报）**物理分离**，互不污染 |
| UT-M5-11 ✅（M5d） | **M5d 下段渲染** | 下段渲染中枢待办；上段子 Agent 内容同时有数据时两段互不串位；中枢无待办时下段显示明确空态 |

**M5d 中枢编排器待办（2026-09-28 已完成）**

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-M5d-1 ✅ | 技能六动作 | `add` 按序 `seq` 自增；`start`→`in_progress`；`complete`→`completed`+`completed_at`；`update` 改内容；`remove` 删除；`list` 回读 |
| UT-M5d-2 ✅ | **物理分离** | 写入中枢待办后 `tasks.todos`（节点上报）**不变** |
| UT-M5d-3 ✅ | 未绑定任务 | 无 `session.task_id` 且无 `parent_task_id` → 明确失败且**不写库**；有 `parent_task_id` 时兜底成功 |
| UT-M5d-4 ✅ | 边界 | 空白内容报错；>500 字符报错；超 50 条报错（**不静默截断**）；非法 `action`/缺 `todo_id`/跨任务 `todo_id` 均失败 |
| UT-M5d-5 ✅ | 只读端点 | 按 `seq` 升序 + `pending_count`/`completed_count`；中枢未写待办 → 空列表；任务不存在 → 404 `201001` |
| UT-M5d-6 ✅ | 前端两段 | 上段 6 区块仍在（既有断言不动）+ 下段渲染中枢待办（含 `已完成/总数`）+ 中枢无待办时空态明确 |
| UT-M5d-7 ⬜ | 用户交互 | **不做**（Q5 裁决为只读展示；`source` 预留 `user`） |
| E2E-A8 ⬜ | 真实链路 | 编排器对话中调用 `todo_write` → 任务摘要下段出现该项（待随真实链路执行） |
| UT-M5-8 | 当前命令可见 | `tool` 落库（✅ 后端）+ 前端在摘要渲染「当前动作」与动作时间线（✅ M5c-2 `TaskInsightPanel`） |
| UT-M5-9 | `@节点` 定向 | `@节点A` 文本被路由为该节点的 steer 消息（含非目标节点不受影响） |

### M6 经验记忆下沉

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-M6-1 | 签名归一化一致 | 中枢 `normalize_signature` 单点实现（节点只上报原文，不自行归一）→ 不存在两端不一致风险 ⬜ **口径已改**（原设计为两端各算一次） |
| UT-M6-2 ✅ | 节点失败入表 | 失败写入 `experience_records`（pending），并带 `source_node_id` + `environment_signature`（`test_experience_env.py`） |
| UT-M6-3 ✅ | 环境签名隔离 | **异环境经验不进入召回**（同环境优先、无命中才回退「环境未知」）；`test_experience_env.py` 专项断言 |
| UT-M6-4 ✅ | 派发前召回 | 按**目标节点环境签名**取 top-N resolved → 渲染 `experiences.md` 作为元规则文档随派发下发（`build_rule_docs_payload` 第 4 段） |
| UT-M6-5 ✅ | 定时整理只出草案 | 整理只写 `status="draft"`、**不生效**（召回只认 resolved）；人工确认（复用 `resolve`）后才进召回 |
| UT-M6-6 ✅ | 降级 | 经验库空/LLM 失败/召回异常 → **不注入、不阻断派发与调度**（两侧均有兜底与用例） |
| UT-M6-7 ✅（新增） | 环境签名健壮性 | 签名稳定且定长；`TILAPIA_ENV_SIGNATURE` 可强制统一；探测失败返回 None（`test_env_signature.py` 3 项） |

### M7 前端视觉反馈

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-M7-1 ✅（代码层） | 文本反馈可用 | 探针输出结构化 JSON：HTTP 状态/标题/结构快照（标题层级 + 可交互元素可访问名）/可见文本摘要/console 错误/失败请求与状态码；**退出码即判据**（0/1/2/3）。`node --check` 通过；落盘由 `test_workspace.py` 覆盖（24 passed） |
| UT-M7-2 ⬜（待构建后） | 截图产物 | `--shots <dir>` 落截图 + 文本快照；需真实浏览器（构建期烘焙后验证） |
| UT-M7-3 ⬜（待构建后） | 无显示器可跑 | 探针用 `chromium.launch()` 默认 headless，不依赖 DISPLAY；待构建后验证 |
| UT-M7-4 ✅（设计保证） | 模型不支持图片时降级 | **默认只输出文本**，截图仅在显式 `--shots` 时产生 → 不支持视觉的模型天然可用 |

## 3. 集成测试

| 编号 | 用例 | 方式 |
|------|------|------|
| IT-A1 | 权限兜底不挂 | 模拟推送丢失 → 任务不被卡死（可用真实 tilacode `serve` + 假中枢） |
| IT-A2 | 重试与打转贯通 | 假 provider 连续返回可重试错误/重复调用 → 断言拦截与上限 |
| IT-A3 | verify 分层执行 | 真工作区（构造含 vitest/pytest 的样例项目）→ 断言层级顺序与结果汇总 |
| IT-A4 | steer 通道贯通 | 中枢发 `hub.task_steer` → 节点转 `send_prompt(steer)` → 会话收到新输入 |
| IT-A5 | 经验跨机召回 | 造两条不同环境签名的经验 → 断言只有匹配环境的进入「同环境」命中 |

## 4. E2E（用户可见的行为）

| 编号 | 场景 | 验证 |
|------|------|------|
| E2E-A1 | **验收红必须不报完成** | 派发一个「故意留有验收不通过」的任务 → 断言任务**不进入 completed**，且最终 `change_summary` 或失败原因含未通过判据 |
| E2E-A2 | **超预算先软后硬** | 派发一个会长跑的任务并把预算调低 → 先观察到「收尾」steer（产出 handoff），再（若仍超）被取消；**不允许直接硬杀** |
| E2E-A3 | 会话镜像可见 | 任务运行中在中枢任务详情能看到该节点会话（默认折叠、展开为完整会话），任务结束后仍可回看 |
| E2E-A4 | 视觉反馈可定位 | 故意改坏一个页面 → 文本反馈能指出错误（console/请求/断言）；截图可在前端查看 |
| E2E-A5（**共用 ADR-060 授权**） | 源码级缓存 E2E | 从源码跑一次真实会话并**逼出一次压缩**，复核 P4 摘要热前缀收益（ADR-060 E2E-7）。**已授权但用户要求暂不执行** |
| E2E-A6 | **中枢 `@节点` 对话** | 任务运行中在中枢输入 `@节点A <指令>` → 断言该指令进入节点活动会话、agent 的行为随之改变，且回复在中枢可见 |
| E2E-A7 | **分段摘要实时可见** | 任务运行中观察中枢任务摘要：**上段**子 Agent 状态/待办随 `todo.updated` 更新、**当前执行命令实时可见**（不需等到下一次轮询才对）；**下段**中枢编排器自己的待办独立显示并随其推进实时更新 |
| E2E-A8 | **中枢待办全生命周期** | 一次真实编排中：中枢创建自己的待办 → 推进 → 勾选完成，全程在任务摘要下段可见；子 Agent 同时也有待办时两段并存不串位 |

## 5. CI 门禁

- **tilacode**：`bun typecheck`（`tsgo --noEmit`）与 `bun test` 均须在 **package 目录内**执行
  （`packages/core`、`packages/opencode`）；**禁止在仓库根目录跑测试**。
- **节点**：`pytest opc-agent-programming/tests` + `ruff check/format`。
- **中枢**：`pytest opc-central-dispatch-hub/tests` + `ruff check/format`。
- **前端**（M7 涉及）：`npm run typecheck` → `npm run lint` → `npm run check:colors` → `npm test` → `npm run build`
  → `npm run test:e2e`（`channel: 'chrome'`，无需后端）。
- **守卫用例（必须存在）**：
  - `UT-M1a-2`——**守 ADR-060 的前缀不变量**（别为了加提示词把 system 装配改回"一坨 join"）；
  - `UT-M4-4` / `UT-M4-5` / **`UT-M4-8`**——「无法验证」必须显式、有红必须不报完成、**且不存在假成功路径**，这是整个闭环的底线（用户明确"怕假成功"）；
  - `UT-M5-6`——旧节点兼容，防协议回退；
  - `UT-M6-1`——签名归一化两端一致，否则跨机经验永远命中不了。
- **已知环境限制（非门禁失败）**：本机沙箱下 `packages/core` 全套有 ~99 项失败，全部属于
  git clone/fetch、models.dev 网络拉取、`cross-spawn` 管道 stdio、ripgrep、watcher、flock
  ——**与代码无关**。评审以「失败集合是否变化」为准，不以「0 失败」为准。

## 6. 构建与分发（B1：本地可编 Linux）

> 记录在此以免上下文丢失。规则见 `opc-agent-programming/tilacode/BUILD.md` 与 ADR-013。

### 6.1 网络实测（2026-09-28，**更正历史结论**）

| 目标 | PowerShell `Invoke-WebRequest` | Python httpx | bun fetch |
|------|-------------------------------|--------------|-----------|
| `registry.npmjs.org` | 失败（schannel） | **200** | **200** |
| `registry.npmmirror.com` | 失败 | **200** | — |
| `github.com` | 失败（schannel） | **200** | **200** |
| `api.deepseek.com/v1/models` | — | 可达（无 key 时 401） | — |

结论：**没有网络封锁**；此前「000 不可达」是 PowerShell schannel 假象。故 npm 安装（如 Playwright 浏览器）
与 Bun 交叉编译下载 Linux 运行时**均应可行**。

### 6.2 构建命令

> ⚠️ **必须同时给 channel 与版本号（2026-09-28 实测发现，会导致节点换库）**：
> `InstallationChannel` 是**构建期注入的常量**（`core/src/installation/version.ts:7`：`typeof OPENCODE_CHANNEL === "string" ? OPENCODE_CHANNEL : "local"`），
> 而节点启动时**不设置** `TILACODE_DB`（`tilacode_manager.py:129` 只加 `TILACODE_SERVER_PASSWORD`），
> 于是 DB 文件名由 channel 决定（`core/src/database/database.ts:43-55`）：
> `latest|beta|prod` → `tilacode.db`；其它 → `tilacode-<channel>.db`。
> 已发布 manifest 是 `1.18.13` 而节点数据目录里是 **`tilacode-local.db`** → 唯一一致的解释是
> **构建时同时设了 `OPENCODE_CHANNEL=local` 与 `OPENCODE_VERSION=<版本号>`**（`script/src/index.ts:27` 的优先级）。
> **只给版本号会让 channel 变成 `latest` → DB 变成 `tilacode.db` → 节点以空库启动、丢掉全部会话历史。**
> 实测确认：按该组合构建时脚本自打印 `{"channel":"local","version":"1.18.13"}`。

```bash
# 交叉编译 Linux（部署用；在一台 Windows 机器上也可执行）
cd opc-agent-programming/tilacode/packages/opencode
OPENCODE_CHANNEL=local OPENCODE_VERSION=<版本号> \
  bun run build --skip-embed-web-ui --target linux-x64-baseline
# 产物：dist/tilacode-linux-x64-baseline/bin/opencode（~140MB，文件名固定为 opencode）

# 本机冒烟（Windows）
bun run build --skip-embed-web-ui --target windows-x64
```

**2026-09-28 实测记录**：在受限环境下 `git`/嵌套 `bun install` 会被拦，可加 `--skip-install`（依赖已装好时）；
成功产物 **147,499,136 字节 / sha256 `6041b5bb8f33ae4871fc42261a763bdf835a80339629bd519a35e892753e7938` / version 1.18.13（channel local）**，
并已用**字符串特征比对**验证 M1a/M2a/M3 的 7 个标记全部进入二进制。

### 6.3 分发（**必须更新 manifest，否则节点不会升级**）

1. 复制到 `opc-central-dispatch-hub/static/binaries/tilacode/<版本>/tilacode-linux-x64`（发布名去掉 `-baseline` 后缀）；
2. 计算 `sha256` 与字节数，回填该目录 `manifest.json` 的 `sha256` / `size`；
3. 升级版本时同步修改 `static/install-scripts/code/install-agent-code-linux.sh` 的 `TILACODE_VERSION`；
4. `manifest.json` 的 `tarball.sha256` 不更新会导致「重跑安装脚本不升级」（BUILD.md:187-188）。

### 6.4 节点侧（Python）变更

任何 `opc-agent-programming` 改动都需重新打包 Agent 模板 tarball 并更新其 `manifest.json`
（`BUILD.md:150-188`），节点为 Nuitka 打包产物。

## 7. 分阶段验收顺序（与 ADR-061 D2 的模块顺序一致）

> **实跑操作单**：[ADR-061 验收操作单（Runbook）](../ops/adr061-acceptance-runbook.md) —— 每条用例的**可复制命令 + 通过标准 + 结果记录表**（含优先级与「已知未真实验证项」）。跑完把结果回填到本表对应的 `UT-*` 行。

| 阶段 | 门槛 |
|------|------|
| M1a | `UT-M1a-1~3` + 与基线对比不劣化（缓存命中率 95.2% / 任务时长） |
| M2 | ✅ `UT-M2-1~5`（M2a）+ `UT-M2-6,7`（M2b 口径修正后）+ `UT-M2b-1~12` + `IT-A1`（含策略随派发下发） |
| M3 | `UT-M3-1~5` + `IT-A2` |
| M1b | 前置勘察通过（确认 LSP 是"接线"还是"移植"）+ `UT-M1b-1~3` |
| M4①②③ | ✅ 已完成：`UT-M4-1~5,7,8,9,10`（判据结构化/优先级/分层超时/无法验证/完成闸/落痕/阻断/24h 兜底/决策）+ `IT-A3` 待补；`E2E-A1` 待真实链路 |
| M5a | ✅ 已完成：`UT-M5a-3~7`（= `UT-M5-1,2,4,6` 的落库/字段/兼容部分）+ 库级与真实落库实证；`UT-M5-9`（`@节点`）与 `IT-A4` 已由 **M5a-steer** 阶段完成 |
| M4④ | ✅ 已完成（P0 所需部分）：`UT-M4-6`（自修 ≤5 轮，含用尽后阻断）；轮次上下文细化留待优化 |
| M5b | ✅ 已完成：`UT-M5-3`（软 80% 只发收尾指令不停任务 / 硬 100% 强制停止 / 阈值可配 / 双维度任一超限 / 幂等）+ `E2E-A2` 待真实链路 |
| M5c | ✅ 已完成：`UT-M5-5`（三条告警规则 + **不误报**）+ `UT-M5-7` 守卫 + `UT-M5-8`（当前命令可见）+ `/insight` 只读端点 + 前端洞察面板与 `@节点` 发言；`UT-M5-9`（`@节点` 定向后端）已由 M5a-steer 覆盖；`E2E-A3/A6/A7` 待真实链路 |
| M5d | ✅ 已完成：`UT-M5d-1~6`（技能六动作 + 物理分离 + 未绑定任务 + 边界 + 端点 + 前端上下两段）；`E2E-A8` 待随真实链路执行 |
| M6 | ✅ 已完成：`UT-M6-2~7`（环境签名/来源/隔离/派发前召回/定时整理只出草案/降级/签名健壮性）+ 中枢 6 项与节点 3 项用例；`UT-M6-1` 口径已改（归一化单点在中枢）；`IT-A5` 待补 |
| M7 | ✅ 代码完成：`UT-M7-1`（文本反馈与退出码判据）+ `UT-M7-4`（默认文本、截图可选）；`UT-M7-2/3` 待**构建期烘焙**（`npm ci` + `npx playwright install chromium`，`PLAYWRIGHT_BROWSERS_PATH=0`）后验证；`E2E-A4` 待真实链路 |
| 收尾 | `E2E-A5`（源码级缓存复核，需用户指定时机）+ 全套回归 |
