# 中枢 Agent Loop 能力差距与改进路线图

> 状态：Roadmap（机制探查档案 + 问题固化清单，各项实施时单独出 ADR 四件套）
> 探查依据：tilacode（opencode 改名版）源码精读（`opc-agent-programming/tilacode/packages/core/src/session/`、`packages/opencode/src/session/`、`packages/opencode/src/tool/`）× 中枢源码对照（`app/agents/orchestrator.py`、`app/clients/deepseek.py`、`app/services/orchestrator_service.py`）
> 创建日期：2026-09-21

---

# 第一部分：tilacode 机制探查档案（固化）

> 以下为 2026-09-21 源码研读结论，按探查章节编号固化，作为复刻依据。

## 二、"多次思考"的实现：续行循环

核心在 `packages/core/src/session/runner/llm.ts` 的 `run()`：

- **外层 `while(shouldRun)`**：处理排队消息（每次取一条）
- **内层 `while(needsContinuation)`**：逐轮调 `runTurn()`，step 递增

**续行判定极其简单**：本轮流式响应中出现过 `tool-call` 事件 → `needsContinuation = true`。
不看 stop reason（部分 provider 会带 tool_call 却返回 stop）。工具结果通过
`toolMaterialization.settle()` **先持久化**再进历史，下一轮重新加载全量历史再调 LLM——
"思考"本质是：**LLM 输出工具调用 → 工具结果落库 → 带着新上下文再问一次 LLM**，
循环直到模型不再要工具。

两个精巧细节：

1. **步数上限**：agent 有 `steps` 上限，到最后一步时禁用所有工具、注入"这是最后一步"
   提示词强制纯文本收尾——避免死循环。
2. **Steering（中途插话）**：执行中用户再发消息，先落库为 `session_input`（不丢），在
   **下一个 Provider Turn 边界**提升进历史，且提升任何新输入会**重置步数配额**。

**持久化与恢复**：一切状态都是 durable event（SQLite `event` 表，`(aggregate_id, seq)`
唯一），流式文本/工具状态增量落库；崩溃后下次 drain 把遗留 pending 工具标 failed
（`failInterruptedTools()`），靠新消息/显式 resume 触发续跑。

## 三、工具调用原理

链路（`packages/opencode/src/session/processor.ts` 为中枢）：

```
工具定义（id + Effect Schema 参数 + description + execute）
  → registry 注册（内置 bash/read/edit/glob/grep/task/fetch/todo/skill...）
  → SessionTools.resolve 转成 AI SDK tool（jsonSchema）
  → streamText({ tools }) 携带给模型 —— 模型选工具就是标准 tool-calling，无自研路由
  → 流式事件驱动：tool-input-start 建 pending ToolPart → tool-call 置 running
  → 执行前：plugin hook → Permission.ask（allow/deny/ask 规则，ask 挂起等用户回复）
  → 执行 → 输出截断 → completeToolCall 落 ToolPart（completed）→ 下轮自动带回
```

关键设计：

1. **工具输出双轨制**：模型可见输出限 2000 行 / 50KB（保留头或尾），完整输出落盘为
   Managed Tool Output File，截断文本附路径提示"用 Grep/Read 处理该文件"。
   **上下文占用有界，信息不丢失**。
2. **bash 工具**（`shell.ts`）：tree-sitter AST 权限预扫描；`Effect.raceAll([exitCode,
   abort, timeout])` 超时强杀（默认 2 分钟）；输出流式推 UI + 超限转文件追加流。
3. **task 子代理**：派生**全新子会话**（父对话不带入，天然上下文隔离），注入工具 deny
   规则防递归，取子会话最后一条文本包成 XML 返回。
4. **doom loop 检测**：连续相同工具调用触发权限询问，防原地打转。

## 四、上下文注入机制

核心概念 **Context Epoch**（`packages/core/src/session/system-context/`）：

- **System Context = 多个 Context Source 组合**：`core/environment`（工作目录/git/平台）、
  `core/date`、`core/instructions`（向上收集 AGENTS.md + 全局配置）、`core/skill-guidance`
  （可用技能清单）。每个 Source 有稳定 key + 加载器 + 三个纯渲染函数（baseline/update/removed）。
- **首轮渲染 Baseline System Context，存库后永不改写**（除压缩/迁移），作为 provider-cache 前缀。
- **运行中某 Source 变化**：在下一个 Provider Turn 边界**追加一条持久化的
  Mid-Conversation System Message**（只含新状态完整值），快照原子推进。
- 请求组装：`system = [agent 系统词, baseline]`，`messages = baseline_seq 之后的全部历史
  （含穿插的 system 增量消息）`。
- **为何 baseline 不可变**：请求前缀逐轮稳定 → 命中 provider prompt cache（OpenAI
  `promptCacheKey`，Anthropic `cache_control: ephemeral`）。命中部分约 1/10 计价；
  baseline 变动一次则后续每轮整段前缀全价重算。

## 五、遇到 bug 的排查思路（Prompt 层面）

系统提示按模型分文件（`packages/opencode/src/session/prompt/`），调试指令反复出现：

> "When debugging, try to determine the **root cause** rather than addressing symptoms"
> "Use print statements, logs, or temporary code to **inspect program state**"
> "**Iterate until** the root cause is fixed and all tests pass"
> "check error logs or failed tests → **scan over the codebase to find the root cause** → figure out a fix"

排查思路 = **指令先行**（根因优先、证据收集、迭代到测试全过）+ **工具循环承接**
（跑测试看报错 → read/grep 定位 → edit → 再跑测试）。多轮循环是骨架，prompt 告诉模型怎么用好骨架。

## 六、上下文压缩（撞上限怎么办）

`packages/core/src/session/compaction.ts`，**双触发 + 单摘要**：

1. **主动触发**：每轮请求前 `Token.estimate(JSON.stringify({system, messages, tools}))`
   估算（`长度/4` 启发式），超 `context_window - max(output, buffer≈20000)` 即压缩。
2. **被动兜底**：捕获 provider context overflow 报错（assistant 未开始输出时）→ 压缩后重跑。
3. **摘要算法**：全历史序列化成带前缀文本（工具结果截 2000 字符），尾部保留最近
   `keep.tokens`（默认 8000）作 `recent`，其余作 `head`，**单独发一次 LLM 请求**生成
   结构化摘要；已有旧摘要时做"锚定增量更新"而非重写。
4. **摘要模板**（固定结构，强制保留精确标识符、不提及压缩）：

```
## Objective / ## Important Details
## Work State（Completed / Active / Blocked）
## Next Move / ## Relevant Files
```

5. **压缩后**：摘要存为 `compaction` 消息，历史投影只加载 `seq >= compaction.seq`；
   降为一条 user 角色 `<conversation-checkpoint>`（summary + recent-context）；
   **开启新 Context Epoch**（旧前缀已截掉，重建 baseline + 快照）。循环用
   `TurnTransitionError` 中断本轮、重建请求重跑。

---

# 第二部分：中枢缺陷清单与路线图

## 1. 背景与结论

中枢的架构分层是对的（中枢决策 + 节点执行），但中枢自身的 Agent Loop（OrchestratorAgent
对话链路）停留在「薄决策循环」：**工具轨迹不回流、上下文不管理、执行无保护、LLM 客户端
太薄、伪流式**。这五块恰恰是 tilacode 最成熟的地方。

| 能力维度 | tilacode（单机编程代理） | 中枢（多节点调度） | 差距 |
|---------|------------------------|------------------|------|
| 工具调用循环 | 双层循环（外层队列 + 内层 needsContinuation），每轮重载持久化历史 | 单层 for 循环（max_rounds=8），prompt_messages 内存累积 | 大 |
| 轨迹持久化 | tool-call/tool-result 全部落库，崩溃可恢复、可审计 | 工具轨迹只在内存；历史仅存 role/content，下一轮即"失忆" | 大 |
| 上下文注入 | system = 角色基线（环境/日期/AGENTS.md/技能清单/参考），messages 全量投影 | system = 静态 prompt + 技能清单 + spec 草案；无环境/日期/项目指令 | 大 |
| 上下文压缩 | 预压（2 万 token 缓冲）+ 溢出兜底 + checkpoint 投影 + 锚定摘要 | 无 token 估算、无压缩；长对话直接撞 DeepSeek 窗口报错 | 大 |
| 工具执行 | 并发执行 + 大输出截断落盘 + 调用失败自动修复 | 串行执行、无技能超时保护、结果仅 summary 无截断 | 中 |
| 流式体验 | 全程真流式（文本/推理/工具事件） | 工具轮非流式；最终回答"生成完再切 4 字符块" = 伪流式 | 中 |
| 安全/权限 | permission 规则（allow/ask/deny） | 技能直接执行无权限层 | 中 |
| LLM 生态 | 多 provider + 专属 prompt + prompt 缓存 + 重试 | 仅 DeepSeek 客户端、无重试/退避、无缓存、错误即结束 | 中 |
| 分布式调度 | 无（单机本地执行） | 多节点心跳/WS 下发/规则预筛 + AI 决策 + 审计 | **中枢领先** |
| 任务上下文传承 | 无此场景 | 任务链父子挂接 + handoff 现场迁移（ADR-028/034） | **中枢领先** |

## 2. 缺陷清单（按严重程度排序）

### 缺陷 1：工具轨迹不持久化、不回流 → 中枢对话「失忆」【P0，本次实施】

`app/agents/orchestrator.py:123-125`：历史只回传 `role/content`，assistant 的
steps/thinking 是前端展示层数据，不进 LLM 请求。后果：

- 上一轮调用了 `find_idle_nodes` 查到节点负载，下一轮问"刚才那个节点呢"，模型完全不知道；
- 崩溃/超时后，整轮工具执行轨迹（调了哪些技能、结果如何）随内存消失，无法恢复、无法审计
  （`dispatch_decisions` 只审计调度决策，不审计对话技能轨迹）。

对标：探查档案**章节二**（轨迹持久化 + 续行循环）。

### 缺陷 2：伪流式 → 长回复首字延迟高、工具执行期完全无反馈【P0，本次实施】

当前实现：最终回答**等 LLM 生成完成后切 4 字符块**假装修流；工具轮执行期间无流式事件。
对标：探查档案**章节三**（工具生命周期事件逐个推送）+ 全程 textDelta 实时转 SSE。

### 缺陷 3：无上下文长度管理 → 长对话必然失败【P1】

无 token 估算/压缩/溢出处理，DeepSeek 窗口撞到就 400 → error 事件结束。
对标：探查档案**章节六**三件套（预压 + 溢出兜底 + 锚定摘要模板）。

### 缺陷 4：上下文注入静态 → 模型对运行环境无感知【P2】

system = 静态 prompt + 技能清单 + spec 草案，无环境信息（当前时间、在线节点概况）。
对标：探查档案**章节四**（Context Source 多源组合 + baseline 不可变 + 增量 system 消息）。

### 缺陷 5：执行无保护 → 死循环烧 token、单技能卡死【P3】

对标：探查档案**章节二**步数上限 + 末轮禁工具；**章节三**doom loop 检测、技能超时。

### 缺陷 6：LLM 客户端太薄 → 无重试/退避/缓存【P4】

错误即结束。对标：探查档案**章节四**prompt cache + 重试降级（部分已被 ADR-029/030 覆盖）。

## 3. 实施路线（P0 → P5 依次解决）

| 优先级 | 事项 | 对标档案章节 | 状态 |
|-------|------|------------|------|
| **P0（本次）** | 多次思考：工具轨迹持久化 + 回流 + 多轮循环重构（缺陷 1） | 章节二 | **已完成（ADR-040，2026-09-21）** |
| **P0（本次）** | 工具调用链路：流式 tool-call 事件 + 结果结构化落库（缺陷 1） | 章节三 | **已完成（ADR-040，2026-09-21）** |
| **P0（本次）** | 伪流式改真流式：文本 delta 实时推送 + 工具执行事件流（缺陷 2） | 章节二/三 | **已完成（ADR-040，2026-09-21）** |
| P1（下一项） | 上下文压缩：token 估算预压 + overflow 兜底 + 五段锚定摘要模板（缺陷 3） | 章节六 | **已完成（ADR-041，2026-09-21，含 context_usage 实时展示与 history_recall 回查技能）** |
| P2 | 上下文注入增强：环境/日期 Context Source + baseline 不可变 + 增量 system 消息（缺陷 4） | 章节四 | **已完成（ADR-042，2026-09-21，日期时间+集群概况 Baseline/增量注入）** |
| P3 | 执行保护：步数上限 + 末轮禁工具 + doom loop 检测 + 技能超时（缺陷 5） | 章节二/三 | **已完成（ADR-040 + ADR-043，2026-09-23）**：末轮禁工具随 ADR-040 落地；技能超时（300s 全局预算，逐事件限时）+ doom loop 检测（连续 3 轮相同签名收尾）随 ADR-043 落地，测试 30 passed |
| P4 | LLM 客户端增强：重试/退避/降级链 + prompt cache 前缀稳定（缺陷 6） | 章节四 | **已完成（ADR-044，2026-09-23）**：重试退避（5 次 1s/2s/4s/8s，可重试错误限定）+ 对话主链路降级（首次调用失败换模型，warning 提示）+ cache 命中落库观测；前缀稳定性已由 ADR-042 环境快照覆盖 |
| P5 | Steering：执行中输入先落库、边界提升 + 配额重置 | 章节二 | **已完成（ADR-045，2026-09-23）**：会话级队列 + 投递端点（无活跃流 409 降级）+ 三处 Provider Turn 边界提升（工具轮后/收尾判定/强制收尾前）+ rounds_used 配额重置 + 落库顺序协调 + 前端解禁输入与排队气泡 |

## 4. 明确不照搬

- Context Epoch 完整 event-sourcing 持久化体系——中枢以「消息表 + 压缩标记列」轻量版达到同等效果；
- tree-sitter 命令权限预扫描——中枢工具为内部 API 调用，无 shell 风险面；
- 权限 allow/ask/deny 体系——中枢技能为白名单内置技能，暂无第三方扩展面；
- 子代理 task 工具——中枢技能即内部函数调用，无子会话场景。

## 5. 验收标准（路线图整体）

- [x] P0 完成后：同会话跨轮次工具结果可被模型引用（问"刚才那个节点呢"能答上）——已验证（ADR-040 端到端：第二轮零技能调用直接引用第一轮工具结果）；
- [x] P0 完成后：中枢重启恢复会话后，历史工具轨迹仍在（可审计）——已验证（JSONB 落库 4 条消息按序完整）；
- [x] P0 完成后：前端收到的是增量文本 delta 与工具生命周期事件（非切块伪流式）——已验证（138 chunk 分散到达 / 562ms 跨度）；
- [x] P1 完成后：长对话（超 DeepSeek 窗口）不再 400 报错结束，自动压缩后继续——已实施并端到端验证（ADR-041）：context_usage 实时推送、预压/overflow 兜底压缩、history_recall 回查原文；2026-09-23 低窗口（1200）会话实测压缩闭环（context_compaction_done，摘要落库圆环回落）；
- [ ] P2-P5 各项实施后逐项补充对应 ADR 的 Harness 验收清单。
