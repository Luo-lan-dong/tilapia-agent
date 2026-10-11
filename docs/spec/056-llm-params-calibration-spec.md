# LLM 调用参数与窗口口径校正 Spec（P2）

> 关联：[ADR-056](../adr/056-llm-params-calibration.md) · [Harness](./056-llm-params-calibration-harness.md) · [Changes](./056-llm-params-calibration-changes.md)
> 上游：[ADR-044](../adr/044-llm-resilience-v1.md)（LLM 客户端韧性）· [ADR-041](../adr/041-context-management-v1.md)（窗口与压缩触发线）
> 官方依据：[Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)、[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)

## 1. 功能描述

把中枢对 DeepSeek 的调用参数与「模型真实能力」对齐，并消除三处语义偏差：

1. **窗口口径**：`context_window` 从 65536 校正为 **1,000,000**（该模型真实 CONTEXT LENGTH），
   压缩触发线与缓冲按新窗口计算；
2. **输出上限**：硬编码 `max_tokens = 8192` 改为按场景分档（默认 32768），并对触顶做告警；
3. **思考模式**：显式下发 `thinking: {"type": ...}`；思考模式下不下发无效的 `temperature`；
   `reasoning_effort` 档位收敛为官方支持的 `low | high | max`；
4. **`reasoning_content` 回传**（ADR-056 决策 D4 路线 B）：带 `tools` 的请求必须回传历史
   assistant 的 `reasoning_content`（复用既有 `thinking` 字段），否则 provider 报 400。

## 2. 用户故事

- **US-1（防浪费）**：作为运维，1M 窗口的模型不应在占用 3.7% 时就触发上下文压缩。
- **US-2（防截断）**：作为用户，长回答（部署收尾、三件套）不应被 8192 输出上限静默截断；
  万一截断，运维能从日志发现。
- **US-3（协议正确）**：作为系统，带 tools 的多轮工具调用必须回传历史推理内容，
  不因思考模式而被 provider 拒绝。
- **US-4（语义诚实）**：参数要么生效、要么不下发——不再发送思考模式下无效的 `temperature`。

## 3. 核心流程

### 3.1 参数组装（`_build_payload`）

```
_build_payload(messages, response_format, reasoning, stream,
               thinking(可选), max_tokens(可选), temperature(可选))
  │
  ├─ thinking is None → 保持旧行为：reasoning=True 时发 reasoning_effort（缺省 medium）
  ├─ thinking is True  → payload["thinking"] = {"type": "enabled"}
  │                      payload["reasoning_effort"] = settings.llm_reasoning_effort
  │                      **不下发 temperature**（思考模式不支持）
  └─ thinking is False → payload["thinking"] = {"type": "disabled"}
                         **不下发 reasoning_effort**
                         temperature = 传入值或 DEFAULT_TEMPERATURE
  max_tokens = 传入档位 or settings.llm_max_output_tokens
```

### 3.2 输出档位

| 场景 | 档位 | 常量/来源 |
|------|------|----------|
| orchestrator 主链路（流式） | `settings.llm_max_output_tokens`（默认 32768） | 配置 |
| 三件套生成（spec_builder） | `SPEC_BUILDER_MAX_OUTPUT_TOKENS = 16384` | 常量 |
| 摘要（compaction） | `SUMMARY_MAX_OUTPUT_TOKENS = 4096` | 常量 |
| 其他（planning/ai_dispatch/标准文档） | 默认档 | 配置 |

触顶告警：`usage.completion_tokens >= 实际下发的 max_tokens` →
WARNING `llm_output_truncated`（含 scene/model/max_tokens/prompt_tokens）。

### 3.3 `reasoning_content` 回传（决策 D4）

```
orchestrator 流式循环
  reasoning_delta 事件 → 累积 reasoning_parts（同时 yield thinking 给前端）
  流结束 → assistant_entry = {
      role: "assistant",
      content: <正文>,
      tool_calls: [...],
      thinking: "".join(reasoning_parts)   ← 新增：承载 reasoning_content
  }

_build_messages(messages, ..., with_tools=是否带 tools)
  assistant 条目：
    基础 = {role, content}
    有 tool_calls → 带 tool_calls
    带 tools 且该条目有 thinking → 追加 reasoning_content = thinking
    （不带 tools 的收尾轮不回传：官方明确会被忽略）
```

## 4. 降级链路

| 异常场景 | 行为 |
|---------|------|
| provider 不认识 `thinking` 参数（自定义 OpenAI 兼容端点） | 400 立即抛出（不重试，ADR-044 语义）；运维可通过关闭 `orchestrator_thinking_enabled` 回退 |
| `llm_reasoning_effort` 配置非法 | Pydantic 枚举校验，启动即报错（environment-config-standards） |
| 某轮 assistant 无 `thinking`（旧会话/模型未返回） | 不回传该条，其余条目正常；协议要求仅针对实际存在的推理内容 |
| 历史有 assistant(tool_calls) 但整轮无任何 `thinking` 可回传 | 记 `llm_thinking_replay_missing` 提示日志；请求照发（官方未要求补空） |
| `completion_tokens` 达上限 | 记 `llm_output_truncated` WARNING；回答不完整由用户重试 |
| 窗口改 1M 后压缩长期不触发 | 属期望（触发线随窗口上移）；单请求体积仍受 provider 限制，超限走既有 overflow 兜底压缩 |

## 5. 数据结构

### 配置项（`app/core/config.py`）

| 配置 | 默认 | 约束 | 说明 |
|------|------|------|------|
| `llm_max_output_tokens` | 32768 | `ge=1024, le=393216` | 默认输出档（该模型 MAX OUTPUT 384K） |
| `llm_reasoning_effort` | `high` | 枚举 `low/high/max` | 思考强度（`medium` 官方映射为 `high`，不提供） |
| `orchestrator_thinking_enabled` | `true` | bool | 编排链路思考开关（D4 路线 B 默认开） |

### assistant 消息（`task_building_sessions.messages` JSONB）—— 无结构变更

```jsonc
{
  "role": "assistant",
  "content": "我先看看可用的部署环境。",
  "tool_calls": [ ... ],
  "thinking": "用户的意图是部署，我需要先确认服务器...",  // 既有字段，现兼作 reasoning_content 来源
  "steps": [ ... ]                                        // 前端展示用
}
```

## 6. 验收标准

- [ ] `llm_providers` 行 `model=deepseek-flash`、`context_window=1000000`；压缩触发线随窗口变为
      `1000000 − 输出预留 − 缓冲`，缓冲满足 `max(20000, 窗口×5%)` 且 ≤100000；
- [ ] 输出上限：编排链路请求体 `max_tokens=32768`；摘要调用 `max_tokens=4096`；
      三件套生成 `max_tokens=16384`（若启用分档）；
- [ ] `completion_tokens` 达上限时日志出现 `llm_output_truncated`；
- [ ] 思考开启时请求体含 `thinking={"type":"enabled"}` 与 `reasoning_effort`，
      且**不含** `temperature`；
- [ ] 思考关闭时请求体含 `thinking={"type":"disabled"}`，**不含** `reasoning_effort`，
      且含 `temperature`；
- [ ] 带 tools 的请求中，历史 assistant（有 `thinking`）条目带 `reasoning_content`；
      不带 tools 的收尾轮不带；
- [ ] `llm_max_output_tokens` / `llm_reasoning_effort` 非法值启动即失败；
- [ ] 存量会话（assistant 无 `thinking`）回流行为不变（回归）。

## 7. 关联文档

- [ADR-056](../adr/056-llm-params-calibration.md)
- [056-llm-params-calibration-harness.md](./056-llm-params-calibration-harness.md)
- [056-llm-params-calibration-changes.md](./056-llm-params-calibration-changes.md)
