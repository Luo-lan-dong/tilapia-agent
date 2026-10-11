# tilacode 前缀缓存命中率优化 Changes 变更清单（P0–P4）

> 关联：[ADR-060](../adr/060-tilacode-prefix-cache.md) · [Spec](./060-tilacode-prefix-cache-spec.md) · [Harness](./060-tilacode-prefix-cache-harness.md)
> 规则：边开发边实时登记（修/增/减 + 函数级明细 + 作用说明），**禁止开发完成后一次性补记**。
> 分阶段推进，每阶段完成向用户汇报后再进入下一阶段。

## P0 计划变更点（当前阶段）

| # | 文件 | 计划变更类型 | 计划函数/类级明细 | 作用说明 |
|---|------|------------|-----------------|---------|
| 1 | `opc-agent-programming/app/executor/event_translator.py` | 修 | 从 tilacode usage 提取 tokens（含 cache read/write）到 `detail.tokens` | 节点侧组装缓存指标 |
| 2 | `opc-agent-programming/app/client/hub_client.py` | 修 | progress 上报携带 `detail` | 送到中枢（复用 ADR-055 出口） |
| 3 | `opc-agent-programming/app/models/schemas.py` | 修 | progress 上报结构增 `detail` | 两侧结构对齐 |
| 4 | `opc-central-dispatch-hub/app/api/v1/endpoints/agent_node_ws.py` | 修 | `_handle_task_progress` 读取 `detail.tokens` | 接收与解析 |
| 5 | `opc-central-dispatch-hub/app/services/tool_call_log_service.py` | 修/复用 | 新增 `kind="llm_usage"` 记录路径 | 指标落库（复用既有表） |
| 6 | 两侧测试 | 增 | UT-86~UT-90 + IT-18/IT-20 | 覆盖与兼容性回归 |

## P1~P4 计划变更点（2026-09-28 勘察修正后重排）

| 阶段 | 文件 | 计划变更类型 | 计划函数/类级明细 | 作用说明 |
|------|------|------------|-----------------|---------|
| P1 | `tilacode/packages/core/test/system-context.test.ts`（新增） | 增 | `initialize`/`reconcile` 基线与增量断言 | 固化「基线逐字节冻结 + 变化只追加」 |
| P2 | 同上 | 增 | `core/date` 跨天增量 + `removed` 语义断言 | 固化「跨天不作废前缀」 |
| P3 | `tilacode/packages/core/test/tool-registry-definitions.test.ts`（新增） | 增 | 连续两次 `materialize()` 定义序列/序列化稳定 | 固化工具声明稳定 |
| P4 | `tilacode/packages/core/src/session/compaction.ts` | 修 | `select` 改为**消息边界**切分；摘要请求改为 `LLMRequest.update(真实 request, …)` 热前缀重放 | 摘要调用从冷请求变热前缀命中 |
| P4 | `tilacode/packages/core/test/session-compaction.test.ts` | 增 | 断言摘要请求 `system`/`tools` 与真实请求一致、`messages` 为真前缀 + 指令 | 验证 P4 |

> **原计划的 P1~P3 代码改造已取消**：勘察确认 V2 会话核心**已经实现**这些目标
> （`packages/core/src/session/runner/llm.ts` + `session/context-epoch.ts` + `system-context/*`），
> 原计划改的 `packages/opencode/src/session/{prompt,system,instruction,llm/request}.ts` 属 V1 旧路径，
> 不在节点链路上（详见 ADR-060「勘察修正」）。**不做无据改动。**

## 变更清单（实际实施明细）

### P0 · 缓存可观测（已完成）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `opc-central-dispatch-hub/app/services/task_event_handler.py` | 增 | `_log_cache_metrics(task_id, node_id, detail)` | 从 `detail.tokens` 提取 `input/output/reasoning/cache_read/cache_write` 与 `cost`，计算 **命中率 = cache_read/(input+cache_read)**，记结构化日志 `task_progress_cache_metrics` |
| `opc-central-dispatch-hub/app/services/task_event_handler.py` | 修 | `_handle_task_progress` 末尾调用 `_log_cache_metrics(...)` | 接上此前被**完全忽略**的 `detail` 字段（这是 P0 的唯一真实缺口） |
| `opc-central-dispatch-hub/tests/services/test_task_event_handler.py` | 增 | UT-86/87/87b/89b 共 4 项 | 正常命中率计算、无 detail 不产生噪音、零 token 不除零、detail 结构异常安全跳过 |

> **后续变更（ADR-061 M5a，2026-09-28）**：`_log_cache_metrics()` 已重构为 `_parse_usage_metrics()`（纯解析）
> + `_record_cache_metrics()`（记日志 **并** 落库 `tool_call_logs(kind="llm_usage")`，迁移 `20260928c_llm_usage_kind`）。
> 日志事件名 `task_progress_cache_metrics` 与命中率口径**均未变**，本表记录的 P0 行为仍然有效；上表函数名保留为当时的实现名。

**节点侧无需改动**（勘察结论，已核实）：
- `app/executor/event_translator.py:422-441` 的 `_step_detail` **早已**从 `session.next.step.ended` 提取
  `tokens{input,output,reasoning}` 与 `cache{read,write}` + `cost`，并组装为 `detail`（ADR-055 P1-8 已落地）；
- `app/client/hub_client.py:445-453 / 1151-1174` 已把 `detail` 放进 `agent.task_progress` payload；
- 既有测试 `tests/test_event_translator.py::test_step_ended_detail_carries_tokens_cost_files` 已断言
  `detail["tokens"]["cache_read"] == 900`（节点侧契约早有覆盖）。
- 数据源契约：`tilacode/packages/schema/src/session-event.ts:170-175` 的 `step.ended.tokens.cache{read,write}`。

**未做（P0 范围内刻意推迟）**：
- `tool_call_logs` 落库（`kind="llm_usage"`）需**扩展 check 约束 + 迁移**（现有允许值见
  `20260927_terminal.py`），为保持 P0「小且零迁移」先只做日志观测——已足以产出基线；
  若后续需要历史查询/看板，再按同一 ADR 追加迁移与查询面。
- `tool` 字段仍未落库（ADR-055 的既有取舍，本阶段不动）。

### P0 基线（2026-09-28 补采，未改代码）

| 数据源 | 口径 | 结果 |
|--------|------|------|
| `~/.local/share/tilacode/tilacode-local.db`（节点**当前**数据目录，`providerID=deepseek`，含 `session_context_epoch` → 确认走 V2） | `session.next.step.ended` 事件的 `tokens.cache.read / tokens.input` | 2 个真实自主体开发会话、37 轮：cache_read 482,560 / input 24,349 → **聚合命中率 95.20%**（首轮 0%，稳定后 96.8%~99.2%；cache_write 恒 0） |

> 说明：这比「等一次节点任务再从中枢 `logs/app.log` 汇总」更直接、更原始，且不需要派发新任务。
> 同时确认 `packages/llm/src/protocols/openai-chat.ts:391-403` 的 `prompt_tokens_details.cached_tokens`
> 解析在 DeepSeek 端**实际生效**（否则 cache_read 不可能非 0），故**不做无据的协议改动**。

### P1~P4 · 实际实施明细（2026-09-28）

| 阶段 | 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|------|---------|--------------|---------|
| P1/P2 | `tilacode/packages/core/test/system-context/context-epoch.test.ts` | **增**（新文件） | `SessionContextEpoch prefix stability` 2 用例：`initialize` 后连续 `prepare` 的 `baseline`/`baselineSeq` **逐字节不变**；source 变化后 baseline **仍不变**、只发布一条 `session.next.context.updated`、投影出 `system` 历史消息、重复 `prepare` 不重复追加 | 把「基线冻结 + 变化只追加」钉进测试（P1/P2 核心不变量） |
| P3 | `tilacode/packages/core/test/session-runner-tool-registry.test.ts` | 增 | `keeps tool declaration order and serialization stable across materializations`：3 次并发 `materialize()` 的 `definitions` 名字序列与 `JSON.stringify` 完全一致 | 工具声明逐字节稳定（改动被保留的工具定义会使缓存从首个变化 token 失效） |
| P4 | `tilacode/packages/core/src/session/compaction.ts` | 修 | 新增导出 `selectForSummary(entries, tokens)`：**按消息边界**切分，返回 `{head, recent, prefix}`；新增导出 `buildSummaryRequest({request, model, prefix, prompt, maxTokens})`：`LLMRequest.update(真实 request, { messages: [前缀消息…, Message.user(指令)], generation })`；导出 `Entry` 类型 | 摘要调用从**冷请求**（无 system、`tools: []`、单条 user）变为**真前缀重放**，命中上次真实请求已持久化的 cache prefix unit |
| P4 | 同上 | 修 | `compactAfterOverflow`：改用 `selectForSummary`；`prefix` 为空时回退为「把上一条 compaction 的 `recent` 放进指令正文」（保留旧行为，覆盖「几条超大工具输出撑爆请求」场景）；体积闸门改为估算**整条摘要请求** | 避免「整体历史都在 recent 预算内」时无法压缩 |
| P4 | `tilacode/packages/core/test/session-compaction.test.ts` | 增 | 5 用例：摘要请求继承真实请求的 `system/tools/model/providerOptions`（字节相等）、`messages` 为真前缀 + 末尾 user 指令、`generation.maxTokens` 覆写且保留 `temperature`；切分**不切断任何一条消息**（用 BEGIN/END 哨兵验证）；历史整体在预算内时 `prefix=[]`；只有 checkpoint 时返回 `undefined`；checkpoint 落入重放前缀 | P4 验收 |
| P4 | `tilacode/packages/core/test/session-runner.test.ts` | 修 | 2 处断言：`userTexts(requests[...])[0]` → `.at(-1)`（摘要指令现在位于**最后一条** user 消息） | 适配改造后的请求形态（行为变更的显式登记） |

**明确不做（勘察修正后取消）**：
- `packages/opencode/src/session/{prompt,system,instruction,llm/request}.ts`（V1 旧路径）**一行未改**——
  节点链路走的是 V2（`packages/core/src/session/**`），改这些文件既无收益又会扩大回归面。
- `packages/llm/src/protocols/openai-chat.ts` 的 cache 用量解析**未改**——实测已生效。

## 登记记录

- 2026-09-27：创建骨架（ADR-060 四件套建立；Spec/Harness 同步完成，未写生产代码）。
- 2026-09-27：**范围与关键有利条件**——勘察发现 `AgentTaskProgressPayload.detail` 出口已存在
  （ADR-055 D2 已落地），故 P0 **无需新协议/新事件/新表**，只扩展 `detail.tokens`；
  同时确认 tilacode **不跟随上游**（用户确认），P1~P4 可直接改源文件，无需 patch 形式维护。
- 2026-09-27：**已知观测缺口**——本机 tilacode 库仅有 1 个空会话（`tokens_cache_read=0`），
  且中枢侧此前未采集节点缓存数据，故**当前无基线**；P0 的首要产出就是基线数字。
- 2026-09-27：**P0 实施完成**。勘察修正了原计划：**节点侧本来就已经采集并上报了
  `detail.tokens`（含 cache read/write）**，真正缺的只是中枢读取——因此 P0 的改动面远小于初版计划
  （中枢单文件 + 4 项测试，**零迁移**）。
- 2026-09-27：**P0 验证**——中枢 `tests/services/test_task_event_handler.py` **36 项全过**
  （含新增 4 项）；节点侧 `tests/test_event_translator.py` **22 项全过**（既有 cache 契约回归）；
  ruff check/format 全过（修正 4 处长行）。
- 2026-09-27：**P0 基线仍待采集**：需一次真实节点任务才能产出数字（本机库无有效样本），
  待用户派发真实任务后从 `logs/app.log` 的 `task_progress_cache_metrics` 汇总。
- 2026-09-28：**重大勘察修正**——节点链路实际走 V2 会话核心（`packages/core/src/session/**`），
  原 C1/C2/C3「结构性缺陷」属 **V1 旧路径**，在节点链路上不成立；P1/P2/P3 由「实施改造」改为
  「以测试固化现状」，仅 P4 保留为实施改造。ADR-060 增补「勘察修正」与「新增结论 6/7」，Spec/本清单同步改口径。
- 2026-09-28：**P0 基线产出**——直接读节点当前数据目录 `~/.local/share/tilacode/tilacode-local.db`
  的 37 个 `session.next.step.ended`，聚合命中率 **95.20%**（cache_read 482,560 / input 24,349）；
  同时证实 DeepSeek 端 `prompt_tokens_details.cached_tokens` 解析生效，**不做无据的协议改动**。
- 2026-09-28：**P1/P2 固化**——新增 `test/system-context/context-epoch.test.ts` 2 用例（基线冻结 + 只追加）。
- 2026-09-28：**P3 固化**——`session-runner-tool-registry.test.ts` 增 1 用例（工具声明顺序/序列化稳定）。
- 2026-09-28：**P4 实施**——`compaction.ts` 摘要调用改为热前缀重放 + 消息边界切分；新增 5 项单测；
  同步更新 `session-runner.test.ts` 2 处断言口径（指令位于最后一条 user 消息）。
- 2026-09-28：**验证**——`packages/core`：`bun test` **984 通过 / 7 跳过 / 99 失败**，99 项失败全为
  **沙箱环境类**（git/网络/cross-spawn 管道/ripgrep/watcher/flock），**改动前后一致**，与本改动无关；
  `test/session-runner.test.ts` 单文件 **83 项全过**；`bun typecheck`（tsgo --noEmit）**退出码 0**。
