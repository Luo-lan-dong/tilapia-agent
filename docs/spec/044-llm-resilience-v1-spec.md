# LLM 客户端韧性 v1 Spec（重试退避 + 对话降级链 + Cache 观测）

> 关联：[ADR-044](../adr/044-llm-resilience-v1.md) · [Harness](./044-llm-resilience-v1-harness.md) · [Changes](./044-llm-resilience-v1-changes.md)
> 路线图定位：P4 LLM 客户端增强（缺陷 6）。

## 功能描述

为中枢 LLM 调用加三层韧性：

1. **重试退避**（DeepSeekClient，全场景）：可重试错误最多 5 次尝试，
   指数退避 1s/2s/4s/8s（±20% 抖动）；
2. **对话降级链**（仅 Orchestrator 主链路）：首次 LLM 调用重试耗尽后，
   自动切换其他已配置模型继续本轮对话，最多降级 1 次；
3. **Cache 观测**：DeepSeek prompt cache 命中/未命中 token 落库 + 命中率日志。

## 用户故事

- **US-1（瞬时故障自愈）**：provider 偶发 429/5xx 或网络抖动时，用户无感——
  重试自动焂住，对话不中断。
- **US-2（持续故障降级）**：使用中模型持续不可用时，首次对话请求自动降级
  到其他已配置模型，用户看到提示「模型 X 持续不可用，已降级到 Y」，对话继续。
- **US-3（cache 效果可量化）**：开发者可从用量记录查 cache 命中率，验证
  环境快照等前缀稳定性优化的收益。

## 核心流程

### 重试退避（client 层）

```
LLM 调用 → 尝试 N（N=1..5）
  ├─ 成功 → 返回
  ├─ 不可重试错误（400/401/403/404/422 等）→ 直接抛 DeepSeekAPIError
  └─ 可重试错误（429/500/502/503/504/网络异常/超时）
        └─ N < 5 → 退避 base_delay × 2^(N-1) ± 20% → 重试
        └─ N = 5 → 抛出最后一次错误
流式：仅首个增量 yield 前的失败可重试；yield 后流中断直接抛（防内容重复）
```

### 对话降级链（orchestrator 层）

```
chat_stream 首次 LLM 流式调用失败（重试耗尽后抛出）
  → 有其他已配置模型？
      ├─ 否 → 原样抛错（前端 error 事件）
      └─ 是 → 取 created_at 最早的 fallback 构造新 client
              → warning 事件「模型 X 持续不可用，已降级到 Y」
              → meta 事件（model=Y，前端更新显示）
              → 用新 client 重跑当前轮流式调用
              → 再失败（已降级过）→ 抛错结束
后续工具轮失败：不降级，按现状报错（防部分输出后重跑造成内容重复）
```

### Cache 观测

```
LLM 响应 usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens
  → _record_usage 提取 → llm_usage_records 两新列
  → 命中率 hit/(hit+miss) 记 debug 日志
```

## 判定细则

| 项 | 规则 |
|----|------|
| 可重试状态码 | 429、500、502、503、504 |
| 可重试异常 | httpx.TransportError / httpx.TimeoutException / 内置 TimeoutError |
| 不可重试 | 其余 4xx（参数/鉴权类，重试无意义） |
| 降级触发点 | 仅本轮对话首次流式调用失败；后续工具轮失败不降级 |
| fallback 选择 | llm_providers 排除使用中，created_at 最早第一个 |
| 降级次数 | 整轮对话最多 1 次；fallback 再失败直接报错 |
| window 更新 | 降级后 context_usage 分母换 fallback 的 context_window |

## 降级链路

| 场景 | 行为 |
|------|------|
| 瞬时 429/5xx | 重试焂住，用户无感 |
| 持续 5xx（重试耗尽）| 首轮 → 降级 + 提示；中途轮 → 报错 |
| 400/401 参数/鉴权错误 | 不重试不降级，直接报错（换模型大概率同样错） |
| 无其他可用模型 | 直接报错 |
| 降级模型再失败 | 报错结束（不无限降级） |

## 验收标准

- [ ] 429 后成功：重试焂住，返回正常结果，日志含 llm_request_retry；
- [ ] 400 不重试：立即抛出，无退避等待；
- [ ] 5 次全失败：抛最后一次错误，总耗时含退避间隔；
- [ ] 流式首帧前失败可重试；首帧后失败不重试（内容不重复）；
- [ ] 对话首轮失败降级：warning + meta 事件、fallback 模型完成对话、
      context_usage 分母为 fallback 窗口；
- [ ] 无其他模型时不降级直接报错；
- [ ] cache 两字段落库（有命中数据时 > 0）；
- [ ] 前端零改动（warning/meta 既有协议）。
