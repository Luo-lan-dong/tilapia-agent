# tilacode 前缀缓存命中率优化 Harness（P0–P4）

> 关联：[ADR-060](../adr/060-tilacode-prefix-cache.md) · [Spec](./060-tilacode-prefix-cache-spec.md) · [Changes](./060-tilacode-prefix-cache-changes.md)
>
> **2026-09-28 重写说明**：本 Harness 初版按「V1 旧路径 + 逐阶段改造」编写。经勘察修正，
> 节点链路实际走 **V2 会话核心**（`packages/core/src/session/**`），P1/P2/P3 已具备、
> 只做**测试固化**，P4 才是实施改造；E2E 基线也改为**直接读节点数据目录**而非另派任务。
> 下方编号保持不变（便于与旧记录对照），但「被测对象 / 文件」按实际实现改写。

## 1. 测试金字塔

```
      E2E（2）          命中率基线（已产出）+ 改造后同口径复核（需重建二进制后执行）
       集成（4）          SessionContextEpoch 会话级冻结/追加 / 摘要热前缀 / 旧节点 detail 兼容
       单元（14）         detail 组装 / 命中率计算 / SystemContext 基线与增量 / 工具声明稳定 / 摘要请求重放
```

## 2. 单元测试清单

### 2.1 P0 缓存可观测（中枢侧 `opc-central-dispatch-hub/tests/services/`）

| 编号 | 用例 | 断言要点 | 实际实现 |
|------|------|---------|---------|
| UT-86 | `detail.tokens` 组装 | usage（含/不含 cache 字段）→ 结构含 `input/output/reasoning/cache_read/cache_write/cost` | 节点侧既有 `_step_detail` 契约，由 `tests/test_event_translator.py::test_step_ended_detail_carries_tokens_cost_files` 覆盖（`cache_read == 900`） |
| UT-87 | 命中率计算 | `cache_read=800, input=200` → 80.0%；两者皆 0 → 0.0%（不除零） | `test_task_event_handler.py` 新增 4 项（含 87b 零 token） |
| UT-88 | 中枢接收 detail | 带 `detail.tokens` 的 progress → 日志 `task_progress_cache_metrics` 含命中率 | 同上（新增用例之一） |
| UT-89 | 旧节点兼容 | 不带 / 结构异常的 `detail` → 校验通过、不报错、不产生噪音日志 | 同上（新增用例之一） |
| UT-90 | `llm_usage` 落库 | `tool_call_logs` 出现 `kind="llm_usage"` | **未做（刻意推迟）**：需扩展 check 约束 + 迁移；P0 只需日志观测即可产出基线 |

### 2.2 P1 system 拆分（tilacode `packages/core/test/system-context/`）

被测不变量：**会话内 `SystemContext` 基线逐字节冻结，易变内容只以追加消息表达。**

| 编号 | 用例 | 断言要点 | 实际实现 |
|------|------|---------|---------|
| UT-91 | stable 逐字节稳定 | source 未变 → `reconcile` 返回 `Unchanged`，`SessionContextEpoch.prepare` 返回**同一个** `baseline`/`baselineSeq` | **新增** `context-epoch.test.ts`「keeps the baseline byte-identical while sources are unchanged」；`system-context/index.test.ts` 既有 `Unchanged` 用例 |
| UT-92 | 首次内容落基线 | 首轮 `initialize` 把各 source 渲染进 `baseline` 并落库快照 | 既有 `system-context/index.test.ts`「loads once and initializes a baseline…」、`builtins.test.ts` |
| UT-93 | 变化只追加、**历史前缀不变** | source 变化 → 只发布一条 `session.next.context.updated`，投影出 `system` 历史消息；`baseline` 字符串**仍等于原值** | **新增** `context-epoch.test.ts`「appends a source change instead of rewriting the baseline」 |
| UT-94 | 未变不重复追加 | 同一新值再次 `prepare` → 不新增事件 | **新增** 同上用例末尾（`contextEvents.length` 恒为 1） |
| UT-95 | 语义等价 / 不丢信息 | 新增 source 用 `baseline` 渲染；source 消失用 `removed` 渲染；`unavailable` 保留已承认快照 | 既有 `system-context/index.test.ts`「uses the baseline for a newly added source」「emits the previously stored removal message」「retains admitted snapshots while a source is temporarily unavailable」 |

### 2.3 P2 env 去波动（tilacode `packages/core/test/system-context/`）

| 编号 | 用例 | 断言要点 | 实际实现 |
|------|------|---------|---------|
| UT-96 | 快照落库 | 首轮 env/date 进 `baseline`；后续轮次不再重渲染基线 | 既有 `builtins.test.ts`「loads location-scoped environment and host-local date context」+ UT-91 |
| UT-97 | 跨天增量 | `TestClock` 拨到次日 → 基线不变，只产出 `Today's date is now: <date>` | 既有 `builtins.test.ts`「reconciles the date without repeating unchanged environment context」；本次新增的 `context-epoch.test.ts` 用同一形状的 source 端到端复核 |
| UT-98 | 同日内不追加 | 同一天 +1 小时 → `Unchanged` | 既有 `builtins.test.ts`「does not update again within the same local calendar day」 |

### 2.4 P3 工具声明稳定（tilacode `packages/core/test/session-runner-tool-registry.test.ts`）

| 编号 | 用例 | 断言要点 | 实际实现 |
|------|------|---------|---------|
| UT-99 | 序列化稳定 | **3 次并发** `materialize()` 的 `definitions` `JSON.stringify` 完全相同 | **新增**「keeps tool declaration order and serialization stable across materializations」 |
| UT-100 | 顺序确定 | 顺序 = 注册表 Map 插入序（进程内固定），非每次重建随机 | **新增** 同上（断言名字序列两两相同；不断言字典序，因 V2 不排序，排序反而会带来一次性失配） |
| UT-101 | 工具集不随轮次增删 | 同一会话相邻两轮工具名集合相同；**已知有界例外**：`isLastStep`（仅当 `agent.steps` 配置时）会清空一次 | 无法用单测覆盖运行时分支，**登记为已知例外**（ADR-060 D4 修正），不写易碎测试 |

### 2.5 P4 摘要热前缀（tilacode `packages/core/test/session-compaction.test.ts`）

| 编号 | 用例 | 断言要点 | 实际实现 |
|------|------|---------|---------|
| UT-102 | 前缀逐字节重放 | 摘要请求的 `system`/`tools`/`model`/`providerOptions` 与真实请求 `JSON.stringify` **全等**；`messages` 前段 = `toLLMMessages(prefix)`，且 = 真实请求 `messages` 的同长度前缀 | **新增**「summary request replays the live request prefix」 |
| UT-103 | 指令置尾 | 最后一条为 user、内容为摘要指令；`generation.maxTokens` 被覆写、`temperature` 等保留 | **新增** 同上 |
| UT-104 | 降级不阻断 | `prefix` 为空（整体历史都在 recent 预算内）时回退为「上一条 compaction 的 `recent` 进指令正文」；只有 checkpoint 时返回 `undefined` | **新增**「summary selection keeps history verbatim while it fits the recent budget」「…only a checkpoint」「selection keeps the checkpoint inside the replayed prefix」 |
| UT-105 | 不切碎消息 | 切分点必须落在消息边界：用 `BEGIN-i`/`END-i` 哨兵断言每条消息整体落在 `head` 或 `recent` 之一 | **新增**「summary selection never splits a message across prefix and recent」 |
| UT-106 | 既有回归 | 压缩触发链路整体不回归（自动压缩、溢出后压缩重试） | 既有 `session-runner.test.ts` 2 项，按新形态更新断言（指令位于最后一条 user 消息） |

## 3. 集成测试

| 编号 | 用例 | 方式 | 状态 |
|------|------|------|------|
| IT-18 | P0 端到端贯通 | 模拟节点上报带 `detail.tokens` 的 progress → 中枢日志可查 | 由中枢单测覆盖（`_handle_task_progress` → `_log_cache_metrics`） |
| IT-19 | 前缀稳定性（会话级） | 两轮之间的 `baseline` 与 `baselineSeq` 逐字节一致、变化只追加 | **新增** `context-epoch.test.ts`（真 sqlite + 真 EventV2 投影） |
| IT-20 | 旧版节点回归 | 不带 detail 的上报全链路无异常 | 中枢单测覆盖（UT-89） |
| IT-21 | 摘要热前缀 | 触发压缩 → 辅助调用 payload 是上一次真实请求的前缀 | **新增** `session-compaction` UT-102 + `session-runner.test.ts` 压缩链路用例 |

## 4. E2E

| 编号 | 场景 | 验证 | 状态 |
|------|------|------|------|
| E2E-6 | **命中率基线** | 直接读节点当前数据目录 `~/.local/share/tilacode/tilacode-local.db` 的 `session.next.step.ended`：2 个真实自主体开发会话、37 轮 → cache_read 482,560 / input 24,349 → **95.20%** | **已完成**（无需另派任务） |
| E2E-7 | 改造后同口径复核 | 重建带本次改动的 tilacode 二进制后，跑同类自主体开发会话（含**至少一次压缩**），按同一 `step.ended` 口径复核：①正常轮次命中率不劣于 95.20%；②**压缩摘要那一次的 `input` 显著下降**（原为整段冷输入，现应大部分命中） | **待执行**（源码已改；节点走 ADR-013 预编译二进制分发，需重新构建/分发后才有生产效果） |

## 5. CI 门禁

- tilacode：`bun typecheck`（在 `packages/core` 内，`tsgo --noEmit`）+ `bun test`（在 `packages/core` 内，
  **禁止在仓库根目录跑测试**）；
- 中枢：`pytest opc-central-dispatch-hub/tests`；节点：`pytest opc-agent-programming/tests`；ruff 通过；
- **守卫用例（必须存在）**：
  - UT-91 / IT-19——「基线逐字节冻结」是总目标的直接断言；
  - UT-93——必须断言**基线字符串未变**（而非仅断言新增了消息），防实现成「重写整个基线」；
  - UT-99——工具声明序列化稳定；
  - UT-102 / UT-105——摘要请求是真前缀且**不切碎消息**；
  - UT-89——旧节点兼容（防协议回退）。
- **已知环境限制（非门禁失败）**：本机沙箱下 `packages/core` 全套 99 项失败全部属于
  git clone/fetch、models.dev 网络拉取、`cross-spawn` 管道 stdio、ripgrep、watcher、flock
  ——与本次改动无关，改动前后失败集合一致。评审时以「失败集合是否变化」而非「是否 0 失败」为准。

## 6. 分阶段验收顺序（实际执行结果）

| 阶段 | 门槛 | 结果 |
|------|------|------|
| P0 | 单测 + 中枢日志可查；产出基线数字 | ✅ 中枢 36 项全过；基线 **95.20%** |
| P1 | 基线逐字节冻结 + 变化只追加 的单测/集成用例 | ✅ `context-epoch.test.ts` 2 项全过 |
| P2 | 跨天增量用例 | ✅ 既有 `builtins.test.ts` + 新增用例 |
| P3 | 工具声明稳定性用例 | ✅ 3 次并发 `materialize()` 序列化全等 |
| P4 | 摘要请求前缀重放 + 不切碎消息 + 降级用例；既有压缩链路回归 | ✅ 新增 5 项 + `session-runner.test.ts` 83 项全过 |
| 收尾 | 全套回归 + typecheck | ✅ 984 通过 / 7 跳过 / 99 沙箱环境失败（改动前后一致）；typecheck 退出码 0 |
