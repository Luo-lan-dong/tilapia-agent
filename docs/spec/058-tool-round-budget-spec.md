# 工具轮预算改为进度驱动 Spec

> 关联：[ADR-058](../adr/058-tool-round-budget-progress-driven.md) · [Harness](./058-tool-round-budget-harness.md) · [Changes](./058-tool-round-budget-changes.md)
> 上游：[ADR-043](../adr/043-execution-guard-v1.md)（Doom Loop / 技能超时）· [ADR-040](../adr/040-orchestrator-agent-loop-v1.md)（多轮循环与三重禁用收尾）

## 1. 功能描述

修复「正常长任务被轮数上限误杀」：把 `orchestrator_max_tool_rounds`
从「唯一防线」降级为「可调安全网」（默认 8 → **40**），
并把提前停止的主要依据交给**进度检测**（连续多轮未获得新信息）。

保留不变：达到上限仍然是「三重禁用 + 强制文本收尾」，且**只在轮次边界检查**、
绝不中断正在执行的技能。

## 2. 用户故事

- **US-1（长任务能跑完）**：作为用户，部署类任务在「配置服务器 → 查清单 → 查指引 →
  定位产物 → 写规划 → 执行」这种多阶段流程中，不应在侦察阶段就被强制收尾。
- **US-2（真打转要停下）**：作为运维，模型真的原地打转时（反复查同一批工具且无新结论）
  仍必须被及时停下，不能烧满 40 轮。
- **US-3（知道为什么停）**：无论是「逼近上限」还是「无新信息」，用户应看到**区分明确**的提示，
  日志里能直接判定原因。
- **US-4（不回归）**：Doom Loop（连续 3 轮同名同参）与技能超时的既有行为不变。

## 3. 核心流程

```
每轮工具轮结束（轮次边界）
  │
  ├─ 1) 进度判定（D2）
  │     新技能集合 = 本轮调用的技能名 − 对话内已成功/失败返回过的技能
  │        ├─ 有新技能 → no_progress_streak = 0
  │        └─ 无新技能 → no_progress_streak += 1
  │     若 no_progress_streak >= orchestrator_no_progress_rounds（默认 4）
  │        → 日志 orchestrator_no_progress_stop
  │        → 注入 ORCHESTRATOR_NO_PROGRESS_PROMPT（文案：连续多轮未获得新信息）
  │        → _stream_plain_answer 收尾（return）
  │
  ├─ 2) 逼近上限警示（D3）
  │     rounds_used >= ceil(max_rounds × 0.75) 且未警示过
  │        → warning 事件「本轮已使用 N/M 轮，任务较复杂」（每请求一次）
  │
  └─ 3) 上限检查（D1，语义不变）
        rounds_used >= max_rounds（默认 40）
          → 日志 orchestrator_max_rounds_reached
          → tools=[] + tool_choice="none" + ORCHESTRATOR_FORCE_FINAL_PROMPT
          → 纯文本收尾
```

**顺序说明**：进度判定优先于上限检查 —— 真打转时不必等满 40 轮。

## 4. 判定细则

| 项 | 规则 |
|----|------|
| 轮数上限默认 | 40（`ge=1`），仍只在轮次边界检查，不中断执行中的技能 |
| 「新技能」定义 | 本轮出现的技能名中，有任意一个**此前从未返回过结果**（成功或失败都算已返回） |
| 无进展连续计数 | 一轮内全部技能都曾返回过 → 计 1；只要有一个新技能 → 清零 |
| 无进展阈值 | `orchestrator_no_progress_rounds`，默认 4（`ge=2`） |
| 警示阈值 | `ceil(max_rounds × orchestrator_rounds_warning_ratio)`，默认 0.75 → 40 轮时第 30 轮警示 |
| 警示次数 | 每次对话请求最多 1 次 |
| 与 Doom 的关系 | Doom（连续 3 轮**完全相同**签名）优先且独立；本检测覆盖「换了参数但无新结论」 |
| 上限收尾 | 三重禁用（tools 为空 + tool_choice="none" + 提示词），ADR-040 语义不变 |

## 5. 降级链路

| 异常场景 | 行为 |
|---------|------|
| 模型每轮都引入新技能（正常长任务） | 无进展计数始终为 0，一路跑到上限（40）或自然收尾 |
| 模型反复用不同参数查同一批工具 | 无进展计数累积 → 达阈值收尾（这是本 ADR 要治的打转形态） |
| 配置 `no_progress_rounds` 非法（<2） | Pydantic `ge=2` 启动即报错 |
| 配置警示比例非法（≤0 或 >1） | Pydantic 校验启动即报错 |
| 技能全部失败（也是信息） | **计入已返回**，不触发无进展（失败本身是结论） |
| 达到上限时正在执行的技能 | 不受影响：检查在轮次边界，技能先跑完（既有语义） |

## 6. 验收标准

- [ ] 模拟「多阶段侦察」会话（连续 6+ 轮各自不同技能）：**不再**被 8 轮上限收尾；
- [ ] 模拟「换参数反复查同一技能」：连续 4 轮无新技能 → 收尾文案为
      「连续多轮未获得新信息」，日志含 `orchestrator_no_progress_stop`；
- [ ] 模拟「连续 3 轮完全相同调用」：Doom Loop 行为与修复前一致（回归 ADR-043）；
- [ ] 达到默认上限 40 时：日志含 `orchestrator_max_rounds_reached`，
      收尾请求 `tools` 为空且 `tool_choice="none"`（回归 ADR-040）；
- [ ] 第 `ceil(max×0.75)` 轮下发一次 `warning`，且同一请求不重复；
- [ ] 技能超时（300s）行为不变（回归 ADR-043）；
- [ ] 前端零改动：仅复用既有 `warning` / `chunk` / `done` 事件。

## 7. 关联文档

- [ADR-058](../adr/058-tool-round-budget-progress-driven.md)
- [058-tool-round-budget-harness.md](./058-tool-round-budget-harness.md)
- [058-tool-round-budget-changes.md](./058-tool-round-budget-changes.md)
