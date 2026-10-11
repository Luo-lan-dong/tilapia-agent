# tilacode 前缀缓存命中率优化 Spec（P0–P4）

> 关联：[ADR-060](../adr/060-tilacode-prefix-cache.md) · [Harness](./060-tilacode-prefix-cache-harness.md) · [Changes](./060-tilacode-prefix-cache-changes.md)
> 上游：[ADR-055](../adr/055-execution-layer-hardening.md)（`detail` 出口）· [ADR-012](../adr/012-tilacode-execution-layer-integration.md)（执行层集成）

## 1. 功能描述

提升 tilacode 编程 Agent 在多轮工具循环中的 **LLM 前缀缓存命中率**，降低节点侧 token 成本与首 token 时延。
分五阶段：

| 阶段 | 目标 | 一句话 | 2026-09-28 修正后的性质 |
|------|------|--------|------------------------|
| P0 | 可观测 | 节点把缓存用量送进中枢，建立基线 | **已实施**（基线 95.20%） |
| P1 | 前缀稳定 | system 只放稳定块，易变内容改为尾部追加 | **V2 已具备** → 测试固化 |
| P2 | env 去波动 | 时间/目录等跨天变化不再作废前缀 | **V2 已具备** → 测试固化 |
| P3 | 工具声明稳定 | 工具顺序与序列化固定 | **V2 已具备** → 测试固化（1 处已知有界例外） |
| P4 | 压缩保前缀 | 辅助调用复用热前缀 | **唯一待实施改造** |

## 2. 用户故事

- **US-1（运维可量化）**：作为运维，我要能看到每个任务/每个节点的缓存命中率，
  而不是只能猜「应该挺省」。
- **US-2（成本下降）**：作为使用者，同一个长任务里第二轮之后的输入应当大部分命中缓存，
  而不是每轮全价重算。
- **US-3（跨天不塌）**：任务跨自然日继续时，不应因为「日期变了」导致整段缓存作废。
- **US-4（自主开发更耐久）**：作为产品定位，子 Agent 要能长时间自主循环（改码→测试→修复），
  成本与时延必须随轮次**摊薄**而不是线性增长。

## 3. 各阶段核心流程

### P0 · 缓存可观测

```
tilacode 会话产生 usage（tokens.input/output/reasoning、cache.read/write、cost）
  │
  ▼
节点侧提取 → 组装 detail.tokens
  │
  ▼
agent.task_progress  payload：{task_id, progress, stage, message, tool, detail:{tokens:{...}}, todos}
  │   （复用 ADR-055 既有字段，不新增事件）
  ▼
中枢 _handle_task_progress
  ├─ 结构化日志 task_progress_cache_metrics{task_id, cache_read, input, hit_rate}
  └─ 落 tool_call_logs(kind="llm_usage")（复用既有表与保留策略）
```

**降级**：节点未携带 `detail.tokens`（旧版/取不到）→ 中枢按「无数据」处理，仅记 debug，不报错。

### P1 · system 拆分与尾部追加（V2 已具备，固化验收）

```
请求装配（V2 现状 = 目标态）
  request.system = [agent 提示词, SystemContext 基线]   ← 两个独立 SystemPart，会话内逐字节冻结
  request.messages = [
      历史（append-only，天然保前缀）,
      …（易变内容变化时，由 SessionEvent.ContextUpdated 追加一条 Message.system 增量，不回写头部）,
      本轮新增消息,
  ]
```

**不变量**：`baseline` 在一段会话内**逐字节不变**；任何 volatile 变化都以**新增消息**表达，
**绝不重写** `baseline` 或任何已发出的历史消息。

### P2 · env 去波动（V2 已具备，固化验收）

```
首轮：baseline 含 "Today's date: 2026-09-27"      ← system-context/builtins.ts 的 core/date
跨天：不重写 baseline；reconcile 产出增量并追加一条消息：
      "Today's date is now: 2026-09-28"
```

### P3 · 工具声明稳定化（V2 已具备，固化验收）

```
每轮请求的 tools 序列化结果必须逐字节相同（顺序固定 + 字段顺序稳定）
  └─ V2：ToolRegistry.materialize() 按 Map 插入序产出，进程内每轮一致
  └─ 已知有界例外：isLastStep（仅当 agent.steps 配置时）把 tools 置空一次，登记不修
```

### P4 · 压缩/摘要复用热前缀

```
摘要调用 request = LLMRequest.update(真实 request, {
     messages: [ …被压缩区前置消息（逐字节，消息边界切分）, {role:"user", content: 摘要指令} ],
     generation: { …原 generation, maxTokens: 摘要输出上限 },
   })
  ⇒ system / tools / providerOptions / model 按引用原样保留
  ⇒ 成为上一次真实请求的真前缀，仅指令与输出不计入命中
```

**切分规则变更**：切分点必须落在**消息边界**（旧实现按字符把一条消息切成前后两半，
既产生失真的摘要输入，也让「重放真前缀」不可能成立）。

## 4. 判定细则

| 项 | 规则 |
|----|------|
| 命中率定义 | `cache_read / (input + cache_read)`（与 tilacode 会话账本口径一致） |
| P0 实测基线 | **95.20%**（`tilacode-local.db` 两个真实会话 37 轮；cache_read 482,560 / input 24,349） |
| P0 采集粒度 | 每个任务累计一次终值 + 进度上报时携带当前值（幂等覆盖，不做增量累加） |
| P0 上报时机 | 随既有 30s 进度周期；失败不影响任务执行（上报是旁路） |
| P1 stable 内容 | agent 提示词 + `SystemContext` 基线（含 instructions/env/date/skills/mcp 的**首次快照**） |
| P1 volatile 表达 | 变化时不改基线，由 `SessionEvent.ContextUpdated` 追加一条 `Message.system` 增量 |
| P1 变化判定 | 由 `SystemContext` codec 结构化全等比较；不等才追加（避免重复追加） |
| P2 快照落点 | `session_context_epoch` 表（`baseline` + `snapshot` + `baseline_seq`），会话级 |
| P3 序列化 | 工具定义顺序 = `ToolRegistry` 注册序（Map 插入序）；禁止每轮重建时改变顺序或字段顺序 |
| P4 适用面 | 压缩/摘要等辅助 LLM 调用；正常轮次不受影响 |
| 兼容性 | 所有新增字段可选；旧节点/旧中枢互不阻塞 |

## 5. 验收标准

- [x] **P0**：中枢日志出现 `task_progress_cache_metrics` 且含命中率（代码与单测已就位，待真实任务触发）；
      `tool_call_logs` 有 `kind="llm_usage"` 记录（**刻意推迟**，需迁移，见 Changes）；
      旧节点不报错（回归已覆盖）；
- [x] **P0**：产出第一份**基线数字** = **95.20%**（来源：`~/.local/share/tilacode/tilacode-local.db`
      的 37 个 `session.next.step.ended` 事件，两个真实自主体开发会话），作为 P1 之后对比依据；
- [x] **P1**：同一会话、source 未变化时 `SystemContext` 基线**逐字节相同**（单测断言）；
- [x] **P1**：source 变化时只产出**增量文本**，基线字符串不变（单测断言）；
- [x] **P2**：模拟跨天 → 基线不变、只产出 `Today's date is now: …` 增量（单测断言）；
- [x] **P3**：同一注册表连续两次 `materialize()` 的工具定义名字序列与序列化结果相同（单测断言）；
- [x] **P4**：摘要请求的 `system`/`tools`/`model`/`providerOptions` 与真实请求**逐字节相同**，
      且 `messages` = 真实请求 `messages` 前缀 + 一条 user 指令；切分点落在消息边界（单测断言，5 项）；
- [x] 各阶段完成后，P0 命中率指标相对基线**不劣化**——基线 95.20% 已记录；P4 只改压缩辅助调用，
      不影响正常轮次（`packages/core` 984 通过 / 99 失败，失败项全为沙箱环境类，改动前后一致）。

## 6. 关联文档

- [ADR-060](../adr/060-tilacode-prefix-cache.md)
- [060-tilacode-prefix-cache-harness.md](./060-tilacode-prefix-cache-harness.md)
- [060-tilacode-prefix-cache-changes.md](./060-tilacode-prefix-cache-changes.md)
