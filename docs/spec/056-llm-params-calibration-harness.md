# LLM 调用参数与窗口口径校正 Harness（P2）

> 关联：[ADR-056](../adr/056-llm-params-calibration.md) · [Spec](./056-llm-params-calibration-spec.md) · [Changes](./056-llm-params-calibration-changes.md)

## 1. 测试金字塔

```
      E2E（1）           真实 API：思考模式下多轮工具调用不 400 + 输出上限生效
       集成（3）          窗口口径 → 触发线 / 存量会话回流回归 / 触顶告警
      单元（14+）         payload 参数矩阵 / 档位选择 / reasoning_content 回流 / 配置校验
```

## 2. 单元测试清单

### 2.1 payload 参数矩阵（tests/clients/test_deepseek.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-45 | `thinking=True` | payload 含 `thinking={"type":"enabled"}`、含 `reasoning_effort` 且值等于配置；**不含** `temperature` |
| UT-46 | `thinking=False` | payload 含 `thinking={"type":"disabled"}`；**不含** `reasoning_effort`；含 `temperature` |
| UT-47 | `thinking=None`（旧行为） | `reasoning=True` → 含 `reasoning_effort=medium` 且不含 `thinking`；`reasoning=False` → 两者皆无（向后兼容） |
| UT-48 | `max_tokens` 显式传入 | payload 使用传入值；不传时使用 `settings.llm_max_output_tokens` |
| UT-49 | `temperature` 显式传入（非思考模式） | 使用传入值；传 `None` 时使用 `DEFAULT_TEMPERATURE` |
| UT-50 | 流式请求同样遵循矩阵 | `chat_completion_stream_raw(thinking=...)` 的 payload 与 UT-45/46 一致 |

### 2.2 档位与触顶告警（tests/clients/test_deepseek.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-51 | 档位常量 | 摘要档 = 4096、三件套档 = 16384、默认档 = 配置值 |
| UT-52 | 触顶告警触发 | `usage.completion_tokens == max_tokens` → 记 `llm_output_truncated`（WARNING） |
| UT-53 | 未触顶不告警 | `completion_tokens < max_tokens` → 无该日志 |

### 2.3 压缩缓冲随窗口（tests/services/test_context_compaction.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-54 | 缓冲下限 | 小窗口（如 65536）→ 缓冲 = 20000（比例值更小，取 max） |
| UT-55 | 缓冲按比例 | 1,000,000 窗口 → 缓冲 = 50000 |
| UT-56 | 缓冲上限 | 极大窗口（如 4,000,000）→ 缓冲封顶 100000 |
| UT-57 | 触发线随窗口 | `compaction_threshold(1_000_000)` = `1_000_000 − 输出预留 − 缓冲`；且真实占用 3.7% 时**不**触发（回归「窗口口径错误」） |

### 2.4 `reasoning_content` 回流（tests/agents/test_orchestrator.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-58 | 带上具回流 | 历史含 `assistant(tool_calls, thinking="推理")` → 带 tools 请求中该条 `reasoning_content="推理"` |
| UT-59 | 不带 tools 不回传 | 收尾轮（`tools=None`/`tool_choice="none"`）的历史 assistant 不带 `reasoning_content` |
| UT-60 | 无 thinking 不回传 | assistant 无 `thinking` 时字段缺省（旧会话回归） |
| UT-61 | 流式累积落库 | `reasoning_delta` 多次 → 轨迹中 `assistant(tool_calls)` 的 `thinking` 为拼接全文 |
| UT-62 | 协议缺口可观测 | 历史有 `assistant(tool_calls)` 但无任何 thinking → 记 `llm_thinking_replay_missing` |

### 2.5 配置校验（tests/core/test_config 或既有配置测试）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-63 | 输出档边界 | `llm_max_output_tokens=1023` / `393217` → 校验失败 |
| UT-64 | effort 枚举 | `llm_reasoning_effort="medium"` → 校验失败；`low/high/max` 通过 |
| UT-65 | 思考开关 | `orchestrator_thinking_enabled` 默认 True，可置 False |

## 3. 集成测试

| 编号 | 用例 | 方式 |
|------|------|------|
| IT-10 | 窗口口径端到端 | 会话以 1M 窗口运行：构造 6 万 token 历史 → **不触发**压缩（旧配置下会触发）；context_usage.percent 反映低占用 |
| IT-11 | 思考模式对话 + 工具回流 | mock 客户端断言：多轮工具调用请求中每轮带 tools 的请求都带上历史 `reasoning_content` |
| IT-12 | 存量会话回归 | assistant 无 thinking 的旧会话跑一轮 → 请求体不含 `reasoning_content`，对话正常 |

## 4. E2E

| 编号 | 场景 | 验证 |
|------|------|------|
| E2E-3 | 真实 API：思考模式下的多轮工具调用 | 真实 `chat/stream` 触发 ≥2 轮工具调用：**无 400**；`llm_usage_records` 的 `completion_tokens` 未触顶（或触顶有 `llm_output_truncated`）；日志含 `thinking: enabled` 语义 |

## 5. CI 门禁

- `pytest tests/clients/test_deepseek.py tests/services/test_context_compaction.py
  tests/agents/test_orchestrator.py` 全绿；
- **守卫用例**：UT-47（`thinking=None` 向后兼容）与 UT-60（旧会话无 thinking 不回流）
  必须存在——防止参数改造破坏存量链路；
- **守卫用例**：UT-57 必须断言「1M 窗口下 3.7% 占用不触发压缩」——这是窗口口径修复的核心回归；
- ruff format/check 全过；前端零改动（`thinking` 事件与字段语义未变）。
