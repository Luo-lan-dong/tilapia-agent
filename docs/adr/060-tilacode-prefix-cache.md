# ADR-060: tilacode 前缀缓存命中率优化（P0–P4）

## 状态

Accepted

## 日期

2026-09-27

## 问题背景

tilacode（`opc-agent-programming/tilacode/`，opencode 的二次开源分支，**不跟随上游**）是编程 Agent 节点的
执行层。它承担「自己写代码、自己测试、自己对接前端」的全自主开发定位，因此**单任务内的 LLM 调用轮次
天然很多**（读文件→改代码→跑命令→看报错→再改）。多轮调用的输入几乎全部重复（system + 历史 + 工具声明），
**前缀缓存命中率直接决定节点侧的 token 成本与响应时延**。

对照 `借鉴/deepseek-harness-master`（`packages/core/agent-loop/README.md:162` 的 KV Cache 规则）：

> 前缀复用要求**早先消息与被保留的工具声明逐字节相同**。替换 system 节点 → 缓存**从该节点首 token 起**
> 失效；改动被保留的工具定义 → 从**首个变化的请求 token** 起失效；**普通历史增长是 append-only，保留复用**。

### 现状勘察（本次代码级确认）

**结论 1：tilacode 的缓存可观测性已齐全，但缺出口。**

`cacheReadTokens` 全链路存在：`packages/llm/script/recording-cost-report.ts`（`promptDetails.cached_tokens`
→ `cacheReadTokens`）→ 会话表 `session.tokens_cache_read` → `packages/stats` 报表与计费模型。
即**不需要自造计量**。

**结论 2：三个结构性缺陷（命中率上不去的根因）。**

| 编号 | 缺陷 | 代码依据 | 后果 |
|------|------|---------|------|
| C1 | **system 是「一坨 join」，任一处变动即从 token 0 作废** | `packages/opencode/src/session/prompt.ts:1257-1269` 把 `[env, instructions, mcp, skills]` 拼成数组；`packages/opencode/src/session/llm/request.ts:58-66` 再 `join("\n")` 成**单条** system 字符串 | `skills` 随 agent/权限变化、`mcp` 随服务器状态变化 → **整条 system 块及其后全部历史**缓存作废 |
| C2 | **`AGENTS.md` 全文进 system，且按路径排序累积** | `packages/opencode/src/session/instruction.ts:155-169`（`systemPaths()` 收集全局/项目/配置声明的文件，逐个读全文拼入） | 新增或改动任一 AGENTS.md → 排序与内容变化 → 又是 token 0 作废；而本项目元规则正是经此注入（ADR-048） |
| C3 | **没有「改动只追加」机制** | 全仓检索无 `systemPromptUpdate` 等价物；`packages/opencode/src/session/compaction.ts:393,434` 只在压缩时重写 system | 参考库的 `in-history` 追加语义缺失，任何 system 变更都只能整段替换 |

**结论 3：`env` 块内含必然漂移项。**

`packages/opencode/src/session/system.ts:60-76` 的 `<env>` 含 `Today's date: <new Date().toDateString()>`、
`Working directory`、`Platform` 等。日期**每自然日必变**，跨天首轮必定整段失配。

**结论 4：节点侧缓存数据在**中枢侧完全不可见**。**

`opc-central-dispatch-hub/app/services/task_event_handler.py` 无 cache/token 字段；
`opc-agent-programming/app/client/hub_client.py` 的 progress payload 亦未携带。
本地实测（`~/.local/share/opencode/opencode-master.db`）仅 1 个空会话、`tokens_cache_read=0`，
**取不到可用于基线的真实样本**。

**结论 5（有利条件）：`agent.task_progress` 的 `detail` 出口已存在。**

`opc-central-dispatch-hub/app/schemas/ws_messages.py` 的 `AgentTaskProgressPayload` 已有
`detail: dict | None`，注释即为「可观测附加信息（ADR-055）：{files, tokens, cost, compaction}」。
**因此 P0 无需新协议、无需新事件、无需新列**，直接复用该出口。

### 勘察修正（2026-09-28 · 关键更正，必读）

**上面结论 2/3/4 针对的是 V1 旧路径（`packages/opencode/src/session/{prompt,system,instruction,llm/request}.ts`），
而节点实际走的是 V2 会话核心（`packages/core/src/session/**`），V1 那套文件在节点链路上不参与请求装配。**
因此 C1/C2/C3 三个「结构性缺陷」**在节点真实链路里并不成立**。更正依据（逐条代码级）：

| 证据 | 位置 | 说明 |
|------|------|------|
| 节点只调 V2 | `opc-agent-programming/app/executor/tilacode_client.py`（`POST /api/session/{id}/prompt`）→ `packages/server/src/handlers/session.ts`（`session.prompt` 处理器）→ `SessionV2.Service.prompt` | V1 `SessionPrompt.loop` 不在节点链路上 |
| 真实请求装配在 V2 runner | `packages/core/src/session/runner/llm.ts:197-214` | `system: [agent.info?.system, system.baseline]` 是**两个独立 SystemPart**，不是 join 成一坨（更正 C1） |
| system 基线会话级冻结 | `packages/core/src/session/context-epoch.ts:50-54,63-65`（`initialize` 落库；`prepare` 命中已存基线即原样返回） | 同一会话内 `baseline` 逐字节不变（更正 C1） |
| 易变内容走**尾部追加** | `context-epoch.ts:72-76` 发布 `SessionEvent.ContextUpdated{text}` → `history.ts:44-46` 纳入历史 → `runner/to-llm-message.ts:134-135` 转 `Message.system(text)` | 等价于参考库 `systemPromptUpdate: 'in-history'`，**从不回写头部**（更正 C3） |
| env/date 已是独立 source | `packages/core/src/system-context/builtins.ts:16-40` | `core/date` 自带 `update`：跨天只追一条 `Today's date is now: …`（更正结论 3） |
| instructions 已是独立 source | `packages/core/src/instruction-context.ts:29-38` | `update` = “These instructions replace all previously loaded ambient instructions.”，追加式（更正 C2） |
| skills 已是独立 source | `packages/core/src/skill/guidance.ts:57-68` | 同上，且按名字排序 |
| registry 顺序确定 | `packages/core/src/system-context/registry.ts:40`（按 key 排序） | source 组合顺序稳定 |
| 工具顺序确定 | `packages/core/src/tool/registry.ts:107-115`（Map 插入序）→ `runner/llm.ts:212` | 同进程内每轮一致（更正 D4 预期） |

**结论 4 更正**：缓存数字**早已能从节点到达中枢**——`runner/publish-llm-event.ts:18-26` 把
`Usage.cacheReadInputTokens` 映射进 `step.ended.tokens.cache.read`，节点侧 `_step_detail` 早已提取上报；
真正缺口只是中枢没读（P0 已补齐）。

**新增结论 6（实测基线，取代原「无有效样本」判断）**：本机 `~/.local/share/tilacode/tilacode-local.db`
（**当前**节点数据目录，会话 `model.providerID = "deepseek"`，含 `session_context_epoch` 表 → 确认走 V2）
中已有两个真实自主体开发会话（`ses_todo_probe_003` / `ses_todo_probe_004`，写 Python todo 项目并自测），
共 37 个 `session.next.step.ended` 事件：

| 指标 | 实测值 |
|------|--------|
| `cache_read` 合计 | 482,560 |
| 非缓存 `input` 合计 | 24,349 |
| **聚合命中率** | **95.20%** |
| 单轮命中率 | 首轮 0%（冷启动）→ 稳定后 96.8% ~ 99.2% |
| `cache_write` | 0（DeepSeek 隐式缓存不计写费） |

即：**V2 路径的逐轮前缀复用已接近最优**；未命中的 4.8% 基本等于「本轮新增的工具结果/用户输入」，属不可压缩项。
因此 **D2/D3/D4 的目标已经达成**，本 ADR 的这三阶段语义由「实施改造」改为「**以测试固化现状**」；
**只有 D5（压缩摘要调用）是真实存在的未修缺口**。

**新增结论 7（DeepSeek 缓存语义已核对）**：官方 Context Caching 文档确认命中以
「完整匹配一个已持久化的 cache prefix unit」为准，持久化点包含**用户输入结束位置**与**模型输出结束位置**。
这解释了实测现象：上一轮模型输出在下一轮**作为已持久化单元被整体命中**，故逐轮 `input` 只剩新增的工具结果。
同时确认官方 usage 字段为 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`；
tilacode 现走 `prompt_tokens_details.cached_tokens`（`packages/llm/src/protocols/openai-chat.ts:391-403`）——
**实测 cache_read 非 0 且稳定增长**，说明 DeepSeek 兼容端点两者都返回，**此项不是缺口，无需改动**（避免无据改动）。

## 决策

**分五阶段推进，每阶段先文档化再实施，且 P1 之后每一步都以 P0 的基线数据验证收益。**
（经 2026-09-28 勘察修正：P1/P2/P3 由「实施改造」调整为「**测试固化 + 只在发现真实缺口时才动代码**」，
P4 仍为实施改造。）

### D1（P0）· 缓存可观测：让节点把缓存数字送到中枢

- **节点侧**：从 tilacode 会话事件/用量中提取 LLM 用量（`tokens.input/output/reasoning`、
  `cache.read/write`、`cost`），组装进既有 `agent.task_progress` 的 **`detail.tokens`**（保持既有键名语义）；
  随 30s 周期上报（或每次 usage 更新后合并到下一次上报）。
- **中枢侧**：`_handle_task_progress` 接收并写结构化日志 `task_progress_cache_metrics`
  （含 `cache_read`/`input`/命中率）；入库位置复用 `tasks` 既有列之外的**不新增表**方案——
  以 `tool_call_logs(kind="llm_usage")` 记录（该表已有 `result_summary` JSONB 与保留策略），
  避免为指标新建表。
- **不做**：不改 `session.tokens_*` 语义（tilacode 自己的账本保持权威）；不把指标塞进 `tasks` 表新列。

### D2（P1）· system 拆「稳定块 / 易变块」，易变内容改为尾部追加

> **2026-09-28 修正**：V2 路径**已实现**本决策（`SystemContext` + `SessionContextEpoch`），
> 本阶段改为「以测试固化该不变量」；仅当测试暴露缺口才动代码。

- **目标**：让 system 在最前、**逐字节稳定**；一切会变的内容不再改写它。
- **做法**：
  - `stable` = agent 提示词（`prompt/*.txt`）+ `AGENTS.md`/instructions（**内容不变时逐字节相同**）；
  - `volatile` = `env`（日期/目录/平台）、`skills`、`mcp`；
  - system 仅承载 `stable`；`volatile` 以**追加消息**形式放在消息序列尾部（首次出现时追加一次，
    之后仅在**内容变化时**再追加一条增量），使已缓存前缀不被改写。
- **对齐参考库**：等价于其 `systemPromptUpdate: 'in-history'`——「非空 prompt 变更追加在已缓存历史之后，
  前缀保持可复用」。
- **约束**：不得改变模型可见语义（内容总量与顺序语义保持一致，仅**位置**从头部移到尾部增量）。

### D3（P2）· `env` 去波动 + 变化增量

> **2026-09-28 修正**：V2 路径**已实现**（`system-context/builtins.ts` 的 `core/environment` / `core/date`
> 与本决策的「快照 + 增量」完全一致），本阶段改为「以测试固化」。

- `Today's date` 等时间信息改为**会话级快照**（首次写入后不变），跨天/跨小时的差异
  经 D2 的**尾部增量**传达，而非重写 system。
- 与中枢侧的 ADR-042（环境 Baseline + 增量）做法保持一致，便于两侧统一心智。

### D4（P3）· 工具声明稳定化

> **2026-09-28 修正**：V2 路径的工具集来自 `ToolRegistry.materialize()`（Map 插入序，进程内稳定），
> 本阶段改为「以测试固化顺序/序列化稳定性」。
>
> **已知有界例外（接受，不修）**：`runner/llm.ts:202-203,212-213` 在 `isLastStep`
> （仅当 `agent.steps` 被配置时才会发生）时把 `tools` 置空并设 `toolChoice: "none"`。
> 该步会放弃工具声明前缀，但**只在受步数上限约束的最后一轮发生一次**；
> 修它需要把「声明保留」与「禁止执行」拆成两个开关，改动面大于收益，故登记为已知例外。

- 核查并固定工具集在请求中的**顺序与序列化**（参考库：改被保留的工具定义会从首个变化 token 起失效）；
- 确认工具集不随轮次增删（若存在按阶段启用工具的逻辑，改为**全集固定 + 提示词引导**，或仅在
  会话首轮确定后不变）。

### D5（P4）· 压缩调用复用热前缀

> **2026-09-28 确认：这是本次唯一真实存在的未修缺口**（`packages/core/src/session/compaction.ts:196-203`
> 构造的是 `messages:[Message.user(summaryPrompt)]` + `tools: []` + **无 system** 的冷请求）。

- 压缩/摘要类辅助调用**重放**当前 `system + 工具 + 被压缩区消息`（逐字节），把指令作为**最后一条 user 消息**追加，
  使其成为上一次真实请求的真前缀。
- 平移中枢侧已验证的做法（ADR-054：摘要调用命中率由 0 → 99.7%）。

## 备选方案

| 方案 | 结论 |
|------|------|
| **A（本决策）P0→P4 分阶段，先量后改** | **采纳**。无基线就改 system 装配，无法判断收益且回归风险高 |
| B. 直接照搬参考库实现 | 否决。两项目栈不同（Cordis 插件 vs Effect + AI SDK），且 tilacode 是独立分支，移植成本高于按原则自研 |
| C. 只做 P1（跳过 P0 可观测） | 否决。没有基线无法验证，且 P1 动的是核心请求装配，必须能回退验证 |
| D. 靠「把 system 缩小」提高命中率 | 否决。缩小体积不等于前缀稳定；命中率取决于**是否逐字节复用**，与体积无直接关系 |
| E. 让节点每次重开会话以规避前缀漂移 | 否决。会丢上下文，与「全自主长任务」定位冲突 |

## 影响

### 节点侧（`opc-agent-programming`）

- P0：`app/executor/event_translator.py` 或 `app/client/hub_client.py` 提取并填充 `detail.tokens`；
  需要从 tilacode 会话用量读取（`session.tokens_*` 或事件流中的 usage）。
- P1~P4：主战场是 **V2 会话核心**（`tilacode/packages/core/src/`）——
  `session/runner/llm.ts`（请求装配）、`session/context-epoch.ts`（基线冻结与增量）、
  `session/compaction.ts`（P4 摘要调用）、`system-context/*`、`instruction-context.ts`、`skill/guidance.ts`、
  `tool/registry.ts`；`packages/opencode/src/session/{prompt,system,instruction}.ts` 属 **V1 旧路径**，
  不在节点链路上，**本 ADR 不再改动它们**。

### 中枢侧（`opc-central-dispatch-hub`）

- P0：`_handle_task_progress` 读取 `detail`、记 `task_progress_cache_metrics` 日志、写
  `tool_call_logs(kind="llm_usage")`。
- 不新增表、不新增事件类型、不破坏 ADR-055 的向后兼容约定。

### 兼容性与风险

- **旧节点**：不携带 `detail.tokens` 时中枢按「无数据」处理，不报错（沿用 ADR-055 的可选字段约定）；
- **P1 是行为变更**：模型可见的消息序列结构改变，必须以 P0 基线 + 回归测试双重验证；
- **上游**：用户已明确 tilacode **不跟随上游**，故 P1~P4 可直接改源文件，无需以 patch 形式维护。

## 关联文档

- [ADR-012](./012-tilacode-execution-layer-integration.md)（tilacode 执行层集成）
- [ADR-019](./019-tilacode-branding-rename.md)（tilacode 品牌化）
- [ADR-024](./024-agent-code-unified-artifact.md)（统一产物与源码保护：改动 tilacode 需注意打包口径）
- [ADR-055](./055-execution-layer-hardening.md)（进度上报协议扩展：本 ADR 复用其 `detail` 出口）
- [ADR-042](./042-context-environment-injection.md)（中枢侧环境 Baseline + 增量：D3 的对齐对象）
- [ADR-054](./054-compaction-boundary-and-prefix-reuse.md)（中枢侧摘要热前缀复用：D5 的平移来源）
- [ADR-048](./048-rule-docs-dispatch.md)（元规则经 AGENTS.md 注入：C2 的成因）
- 参考依据：`借鉴/deepseek-harness-master/packages/core/agent-loop/README.md`（KV Cache effect）、
  `packages/compaction/compaction-basic/README.md`（摘要复用热前缀）、
  `packages/core/agent-loop/tests/request-cache.e2e.ts`（真实 API 命中断言）
- [060-tilacode-prefix-cache-spec.md](../spec/060-tilacode-prefix-cache-spec.md)
- [060-tilacode-prefix-cache-harness.md](../spec/060-tilacode-prefix-cache-harness.md)
- [060-tilacode-prefix-cache-changes.md](../spec/060-tilacode-prefix-cache-changes.md)

## 实际实施情况（分阶段登记）

### P0 · 缓存可观测（2026-09-27 完成）

- **实施面比计划小得多（勘察修正）**：节点侧 `_step_detail`（`event_translator.py:422-441`）**早已**
  从 `step.ended` 提取 `tokens{cache.read,write}` 并放进 `detail`，`hub_client` 也已透传；
  **真正缺口仅在中枢**——`_handle_task_progress` 完全忽略 `detail`。
- **改动**：新增 `_log_cache_metrics()` 并在 `_handle_task_progress` 末尾调用，输出
  `task_progress_cache_metrics`（含 `cache_hit_rate = cache_read/(input+cache_read)`），旁路无副作用。
- **刻意推迟**：`tool_call_logs` 落库需扩展 check 约束 + 迁移，为保持 P0 零迁移先只做日志观测。
- **验证**：中枢 36 项（新增 4）、节点 22 项（既有 cache 契约回归）全过；ruff 全过。
- **基线已产出（2026-09-28 补）**：改用**直接读取节点当前数据目录**
  `~/.local/share/tilacode/tilacode-local.db` 的 `session.next.step.ended` 事件（比等中枢日志更快、更原始），
  得到两个真实自主体开发会话共 37 轮的聚合命中率 **95.20%**（见「勘察修正 · 新增结论 6」）。
  该数字即 P1~P4 的对比基准。
- 剩余可选（未做）：`tool_call_logs(kind="llm_usage")` 落库 + 查询面，仅在需要历史看板时再追加迁移。

### P1 · system 拆分（2026-09-28 · 以测试固化，无生产改动）

- **勘察修正**：V2 路径已按「稳定基线 + 尾部增量」装配，无需改造（依据见「勘察修正」表）。
- **本阶段产出**：新增回归测试固化两条不变量：
  (1) 同一会话、source 未变化时，`SystemContext.initialize/reconcile` 产出的 `baseline` **逐字节不变**；
  (2) source 变化时 `reconcile` 返回 `Updated` 且**只给出增量文本**，原基线字符串不被改写。

### P2 · env 去波动（2026-09-28 · 以测试固化，无生产改动）

- **勘察修正**：`core/date` / `core/environment` 已按「快照 + 增量」建模。
- **本阶段产出**：测试固化「日期从 D1 变到 D2 时，`reconcile` 只产出 `Today's date is now: D2` 的增量，
  且基线（含 D1）保持原样」；并固化 `remove` 语义（source 消失时产出 `removed` 文本而非重写基线）。

### P3 · 工具声明稳定化（2026-09-28 · 以测试固化 + 登记已知例外）

- **勘察修正**：工具集顺序来自注册表 Map 插入序，进程内每轮一致；无按阶段增删工具的逻辑。
- **本阶段产出**：测试固化「同一注册表连续两次 `materialize()` 的 `definitions` 名字序列与序列化结果完全相同」。
- **已知有界例外**：`isLastStep` 时的 `tools: []`（见 D4 修正），登记不修。

### P4 · 压缩调用复用热前缀（2026-09-28 · 唯一实施改造）

- **缺口**：`compaction.ts` 的摘要调用是冷请求（无 system、`tools: []`、只有一条 user 消息）。
- **改造**：
  - 新增 `SessionCompaction.selectForSummary(entries, tokens)`：按**消息边界**切分（旧实现在预算不足时
    按字符把同一条消息切成前后两半，既让「重放真前缀」不可能成立，也给摘要模型喂了被切碎的消息）；
    返回值同时给出 `prefix`（将被摘要替换的消息，含可能的 compaction checkpoint）与 `recent`（原文保留）。
  - 新增 `SessionCompaction.buildSummaryRequest(...)`：以 `LLMRequest.update(input.request, { messages, generation })`
    构造摘要请求，使 `model`/`system`/`tools`/`toolChoice`/`providerOptions` **逐字节保留**（实测：Schema 类构造会
    重建嵌套值，故断言按 `JSON.stringify` 字节相等而非引用相等），`messages` = 被压缩区消息的真前缀 + 末尾一条
    user 摘要指令，`generation` 仅覆写 `maxTokens`（其余采样参数保留）。
  - `prefix` 为空时（整体历史都在 recent 预算内，例如几条超大工具输出撑爆请求）**保持旧行为**：
    把上一条 compaction 的 `recent` 作为指令正文传入；否则指令只带 `previousSummary` 与模板（历史已在前缀里）。
  - 体积闸门由「只估指令文本」改为「估整条摘要请求（system+messages+tools）≤ 上下文 − 摘要输出」。
- **验证**：`packages/core` 单测 984 通过 / 7 跳过 / 99 失败——99 项全部为**沙箱环境失败**
  （git clone/fetch、models.dev 网络拉取、`cross-spawn` 管道、ripgrep、watcher、flock；
  管道类失败正是「沙箱禁止管道 stdio」这一已知边界），**与本次改动无关且改动前同样失败**。
  其中 `test/session-runner.test.ts` **83 项全过**（含 2 项压缩回归，断言按新的「指令在最后一条 user 消息」口径更新）。

### 收尾 · 阶段验证汇总（2026-09-28）

| 阶段 | 结论 | 证据 |
|------|------|------|
| P0 | 已实施 + **基线 95.20%** | 中枢 36 项全过；`tilacode-local.db` 37 个 `step.ended`（cache_read 482,560 / input 24,349） |
| P1 | **V2 已具备**，测试固化 | 新增 `test/system-context/context-epoch.test.ts`：基线跨轮次逐字节相同、变化只追加、重复不重复追加 |
| P2 | **V2 已具备**，测试固化 | 同上（跨天增量文本 `Today's date is now: …`）+ 既有 `system-context/builtins.test.ts` |
| P3 | **V2 已具备**，测试固化 | 新增 ToolRegistry 用例：3 次并发 `materialize()` 名字序列与 JSON 序列化完全一致 |
| P4 | **已实施并验证** | 新增 5 项 `session-compaction` 用例（前缀重放/不切消息/边界语义）+ 2 项既有 runner 压缩回归 |

**结论**：节点链路的逐轮前缀复用**本来就已接近最优**（95.2%），本次真正修掉的只有
「压缩摘要调用是冷请求」这一处结构性浪费；P1~P3 的价值在于**把这些不变量钉进测试**，
避免后续改动（例如有人把 `SystemContext` 基线改回每轮重算）悄悄毁掉命中率。

- 剩余可选项：`tool_call_logs(kind="llm_usage")` 落库 + 查询面/看板（需迁移，未做）；
  `isLastStep` 的 `tools: []` 例外（登记不修，见 D4）。
