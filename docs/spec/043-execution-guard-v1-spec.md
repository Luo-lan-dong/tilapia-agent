# 执行保护 v1 Spec（技能超时 + Doom Loop 检测）

> 关联：[ADR-043](../adr/043-execution-guard-v1.md) · [Harness](./043-execution-guard-v1-harness.md) · [Changes](./043-execution-guard-v1-changes.md)
> 路线图定位：P3 执行保护（缺陷 5），补齐 ADR-040 未覆盖的两项。

## 功能描述

为中枢 Orchestrator 对话循环的两类失控场景加保护：

1. **技能超时**：单个技能执行超过 300 秒（可配置）即中止，对话继续；
2. **Doom Loop 检测**：模型连续 3 轮（可配置）发出完全相同的技能调用时，
   提前中断循环并强制收尾，不再真实执行重复调用。

## 用户故事

- **US-1（防悬挂）**：作为用户，当某技能内部卡死时，我不希望对话永远停在
  「回答中」——最多等 300 秒，应看到该步骤标记失败并收到「技能执行超时」
  的说明，AI 基于其余结果继续回答。
- **US-2（防打转）**：作为用户，当模型反复发出一模一样的调用时，我不希望
  烧满 8 轮才停——第 3 次重复时应看到「检测到重复的工具调用」提示，
  AI 直接基于已有结果作答结束。

## 核心流程

### 技能超时流程

```
chat_stream 工具轮 → _run_tool_call
  → step_started 下发
  → asyncio.timeout(300s) 包裹 skill.execute 消费循环
      ├─ 正常完成 → step_finished(success/failed) → tool 消息回传
      └─ TimeoutError → 失败 SkillResult（"技能执行超时（300 秒），已中止"）
                        → step_finished(failed)
                        → tool 消息回传（协议防 400）
                        → session.rollback()（丢弃未提交事务）
  → 对话循环继续（模型可重试/换思路）
```

### Doom Loop 检测流程

```
每轮拿到 tool_calls
  → 计算签名 sig = json.dumps([{name, arguments 规范化}], sort_keys=True)
  → 追加进最近签名队列
  → 队列末尾连续 threshold(3) 个 sig 相同？
      ├─ 否 → 正常执行本轮全部工具调用
      └─ 是 → 本轮工具调用全部【不执行】
              → 每个 tool_call 补 tool 消息（"检测到连续重复调用，本次未执行"）
                进 prompt + 轨迹（协议合法 + 审计）
              → warning 事件下发前端
              → 注入 doom 收尾提示 → 不带 tools 流式收尾 → 本轮结束
```

## 判定细则

| 项 | 规则 |
|----|------|
| 签名规范化 | arguments 经 `json.loads` 后 `json.dumps(sort_keys=True)`，参数顺序无关；解析失败按原样字符串 |
| 连续判定 | 最近签名队列末尾 threshold 个签名完全一致；窗口滑动，A,B,A,B 交替不触发 |
| 阈值语义 | threshold=3 表示第 3 次相同调用**不执行**（保留 1 次重试余量：第 2 次仍放行） |
| 多工具轮 | 一轮含多个 tool_calls 时整轮算一个签名 |

## 降级链路

| 场景 | 行为 |
|------|------|
| 技能超时 | 失败结果回传 → 循环继续 → 模型重试（重试也超时则每轮最多烧一次 300s，doom/步数上限兜底） |
| doom 命中 | 不执行 + 收尾，绝不大循环 |
| 超时 + doom 同时 | doom 判定优先（签名在执行前比较），命中的轮根本不执行，无超时问题 |
| 配置非法（如 threshold<2） | Pydantic Settings `ge` 约束启动即报错 |

## 验收标准

- [ ] 技能挂起超过配置时长：step_finished(failed) 下发、tool 消息回传、
      对话循环继续（模型收到超时说明）、session 无脏事务残留；
- [ ] 技能正常完成（含耗时 < 超时）：行为与现状一致，无额外开销；
- [ ] 连续 3 轮相同调用：第 3 轮不执行工具、warning 事件下发、
      对话以最终回答结束、轨迹中重复调用带「未执行」tool 消息；
- [ ] 交替重复（A,B,A,B）与间隔重复（A,A,B,A）不触发 doom 收尾；
- [ ] 超时与 doom 均有单测覆盖；前端零改动（复用 warning/step_finished 协议）。
