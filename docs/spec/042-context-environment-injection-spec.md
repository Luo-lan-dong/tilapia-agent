# 环境上下文注入 v1 Spec

> 关联：[ADR-042](../adr/042-context-environment-injection.md) · [Harness](./042-context-environment-injection-harness.md) · [Changes](./042-context-environment-injection-changes.md)
> 路线图：[040-orchestrator-agent-loop-roadmap.md](./040-orchestrator-agent-loop-roadmap.md) P2

## 1. 功能描述

对话链路注入环境上下文：日期时间与集群概况作为 Environment Context Source，
首轮写入 system Baseline（会话内不可变），环境变化时以增量 system 消息
（`<environment-update>`）追加到历史尾部，provider cache 前缀会话内稳定。

## 2. 用户故事

- 作为用户，我说"明天派发任务"，模型知道明天是哪天。
- 作为用户，我问"现在有几个节点能用"，不调技能模型也能给出在线概况。
- 作为运维，长会话的 LLM 成本不因环境信息注入而显著上涨（缓存友好）。

## 3. 环境块格式

```
<environment>
当前时间：2026-09-21（星期一）21时（Asia/Shanghai）
集群概况：2 个节点在线——code 类型 2 个（idle 1 / busy 1）
对话模型：deepseek-v4-flash
</environment>
```

- 时间粒度到小时（跨小时才变化）；星期与时区显式标注；
- 集群概况仅统计在线节点（starting/idle/busy），离线不计；
- 集群概况查询失败时降级为仅时间行。

## 4. 注入流程

```
chat_stream 开始
  → 读会话行 context_environment 快照
  → 生成当前实时块 current
  ├─ 无快照（首轮）：
  │    system baseline 尾部拼入 current；快照落库
  └─ 有快照：
       system baseline 尾部拼入【快照版本】（非实时——保证前缀稳定）
       current != 快照块？→ prompt_messages 尾部追加
         {"role":"system","content":"<environment-update>{current}</environment-update>"}
         并更新快照落库
```

## 5. 降级链路

| 异常场景 | 降级策略 |
|---------|---------|
| 集群概况查询失败 | 环境块仅含日期时间行 |
| 会话行读取失败 | 按无会话处理：实时块每轮注入 system |
| 增量落库失败 | 仅告警，对话继续（下一轮重试对比） |

## 6. 验收标准

- [ ] 首轮对话 system prompt 尾部含 `<environment>` 块（日期时间 + 集群概况）；
- [ ] 同会话第二轮（环境未变）：system 内容与首轮一致（不含增量消息）；
- [ ] 模拟节点状态变化后：prompt 尾部出现 `<environment-update>` 增量消息且快照更新；
- [ ] 无会话对话：环境块每轮注入但不落快照（回归）；
- [ ] 全部既有测试回归通过，新增单测通过。
