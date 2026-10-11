# ADR-044: LLM 客户端韧性 v1（重试退避 + 对话降级链 + Prompt Cache 观测）

## 状态

Accepted

## 日期

2026-09-23

## 问题背景

路线图（040-orchestrator-agent-loop-roadmap.md）P4 缺陷 6：LLM 客户端太薄——

1. **无重试**：`DeepSeekClient` 任何 API 错误（429 限流 / 5xx 服务端抖动 /
   网络瞬断 / 超时）直接抛 `DeepSeekAPIError` → 对话以 error 事件结束。
   瞬时故障本可通过重试焂住，现在用户看到的是「对话失败」弹窗。
2. **无降级链**：单模型绑定，使用中模型持续不可用（provider 故障、模型
   下线、key 失效）时只能等用户手动到设置页切换，对话期间无法自愈。
3. **cache 命中无观测**：DeepSeek 自动 context caching（前缀命中部分约
   1/10 计价），响应 usage 自带 `prompt_cache_hit_tokens` /
   `prompt_cache_miss_tokens`，当前被丢弃——前缀稳定性优化（ADR-042 环境
   快照等）的效果无法量化验证。

tilacode 对标（探查档案章节四）：多 provider + prompt cache + 重试降级。

## 决策

与用户确认（2026-09-23）：**仅对话主链路降级**；**5 次尝试，退避
1s/2s/4s/8s**；**cache 观测落库 + 日志**。

1. **重试/退避（DeepSeekClient 层，全场景生效）**。

   - 配置：`llm_retry_max_attempts=5`（总尝试次数）、
     `llm_retry_base_delay_seconds=1.0`（指数退避 1s/2s/4s/8s，±20% 抖动）；
   - 可重试：`DeepSeekAPIError.status_code ∈ {429, 500, 502, 503, 504}` 与
     网络层异常（`httpx.TransportError` / `httpx.TimeoutException` /
     内置 `TimeoutError`）；
   - 不可重试：其余 4xx（400/401/403/404/422 等，参数/鉴权类错误重试无意义）
     直接抛出；
   - **流式限制**：仅「首个增量产出前」的失败可重试（连接建立 + 状态码检查
     阶段）；首帧 yield 之后流中断不重试——前端已收到部分内容，重试会导致
     重复输出；
   - 重试记 warning 日志（`llm_request_retry`，含 attempt/delay/错误摘要）。

2. **对话降级链（仅 Orchestrator 主链路）**。

   - 触发条件（三者同时满足）：①本轮对话**首次** LLM 流式调用失败
     （后续工具轮失败不降级——中途断流时前端已收到部分输出，换模型重跑
     会造成内容重复）；②重试已耗尽（瞬时故障已被重试焂住，仍失败说明
     持续性故障）；③配置了其他可用模型（`llm_providers` 表排除使用中，
     取 created_at 最早的第一个）；
   - 行为：构造 fallback `DeepSeekClient`（scene=orchestrator）重跑当前轮
     流式调用；向前端下发 `warning` 事件（「模型 X 持续不可用，已降级到
     Y」）与 `meta` 事件（更新模型显示，前端 setModel 幂等）；
   - 限制：整轮对话最多降级 1 次，降级模型也失败则报错结束（error 事件）；
   - `model_window`（ADR-041 占用分母）随 fallback 配置的 context_window
     更新。

3. **Prompt Cache 观测（落库 + 日志）**。

   - `llm_usage_records` 表新增 `prompt_cache_hit_tokens` /
     `prompt_cache_miss_tokens` 两列（INTEGER 默认 0，alembic 迁移）；
   - `_record_usage` 提取 usage 两字段落库；命中率
     `hit / (hit + miss)` 记 debug 日志；
   - 仪表盘展示不在本次范围（数据已可查，页面后续迭代）。

4. **明确不做（本次）**：多 provider 凭据轮换、断路器（连续失败熔断）、
   请求级权重路由、非对话场景降级（spec_builder/ai_dispatch 是短调用，
   重试即可，换模型有格式兼容风险）。

## 备选方案

| 方案 | 结论 |
|------|------|
| 所有 LLM 调用场景统一降级 | 否决（用户确认）：实现重（client 需持 DB 或独立包装层），长链路换模型上下文兼容风险大 |
| 不做降级仅重试 | 否决（用户选择对话主链路降级）：持续性故障重试无法自愈 |
| 3 次尝试 1s/2s 退避 | 否决（用户选 5 次 1s/2s/4s/8s）：网络不稳定环境需要更顽强 |
| `asyncio.timeout` 包整个流式调用 | 否决：流式总时长不可预估（长回复数十秒），超时误杀 |
| cache 命中仅日志 | 否决（用户选落库）：历史命中率无法回溯，无法验证前缀优化效果 |

## 影响

### 对中枢后端的影响

- `app/core/config.py`：新增 `llm_retry_max_attempts`、
  `llm_retry_base_delay_seconds`；
- `app/clients/deepseek.py`：`_is_retryable_error()` 判定 + 非流式方法重试
  循环 + 流式方法「首帧前可重试」循环；`_record_usage` 提取 cache 字段；
- `app/models/llm_usage_record.py` + alembic 迁移：cache 两列；
- `app/agents/orchestrator.py`：`chat_stream` 首次流式调用的降级分支
  （fallback 选择 + warning/meta 事件 + client/window 更新）。

### 对前端的影响

- 零协议新增：复用 `warning`（降级提示）与 `meta`（模型显示更新）既有事件；
  前端已实现二者处理，无需改动。

### 兼容性

- 默认参数下正常调用零开销（无重试触发时仅一次判定）；
- 重试仅作用于幂等安全的对话请求（LLM API 无副作用）；
- 降级仅在首轮失败时发生，不会重复下发任何内容事件。

## 关联文档

- [040-orchestrator-agent-loop-roadmap.md](../spec/040-orchestrator-agent-loop-roadmap.md)（P4 缺口定义）
- [ADR-040](./040-orchestrator-agent-loop-v1.md)（流式协议，降级复用其事件流）
- [ADR-041](./041-context-management-v1.md)（context_window 元数据，降级后 window 更新）
- [ADR-042](./042-context-environment-injection.md)（前缀稳定性已就绪，本决策提供观测手段）
