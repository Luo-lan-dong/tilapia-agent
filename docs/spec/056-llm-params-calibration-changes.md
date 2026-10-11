# LLM 调用参数与窗口口径校正 Changes 变更清单（P2）

> 关联：[ADR-056](../adr/056-llm-params-calibration.md) · [Spec](./056-llm-params-calibration-spec.md) · [Harness](./056-llm-params-calibration-harness.md)
> 规则：边开发边实时登记（修/增/减 + 函数级明细 + 作用说明），**禁止开发完成后一次性补记**。
> 范围约束：只覆盖 D1（窗口口径）、D2（输出分档+触顶告警）、D3（思考模式显式化+参数语义）、
> D4（`reasoning_content` 回传，路线 B）。**不含**工具结果裁剪（P2 剩余项，未立项）。

## 计划变更点（编码前骨架）

| # | 文件 | 计划变更类型 | 计划函数/类级明细 | 作用说明 |
|---|------|------------|-----------------|---------|
| 1 | `app/core/config.py` | 增 | `llm_max_output_tokens` / `llm_reasoning_effort` / `orchestrator_thinking_enabled` | 输出档、思考强度、思考开关 |
| 2 | `app/clients/deepseek.py` | 修 | 常量 `SUMMARY_MAX_OUTPUT_TOKENS=4096` / `SPEC_BUILDER_MAX_OUTPUT_TOKENS=16384`；`_build_payload` 增 `thinking`/`max_tokens`/`temperature` 参数 | 参数矩阵与档位 |
| 3 | `app/clients/deepseek.py` | 修 | `_record_usage` 或流式末帧处理增触顶告警 `llm_output_truncated` | 截断可观测 |
| 4 | `app/services/context_compaction_service.py` | 增/修 | `compaction_buffer_tokens(context_window)`；`compaction_threshold` 改用之 | 缓冲随窗口比例+上下限 |
| 5 | `app/services/context_compaction_service.py` | 修 | 摘要调用传 4096 档 | 摘要输出上限 |
| 6 | `app/agents/orchestrator.py` | 修 | 流式循环累积 `reasoning_parts` → `assistant_entry["thinking"]` | 承载 reasoning_content |
| 7 | `app/agents/orchestrator.py` | 修 | `_build_messages` 增「带 tools 时回流 reasoning_content」逻辑 | 协议要求 |
| 8 | `app/agents/orchestrator.py` | 修 | `chat_completion_stream_raw` 调用传 `thinking=settings.orchestrator_thinking_enabled` | 思考显式化 |
| 9 | Alembic 迁移 | 增 | 数据修正：`llm_providers` 旧模型名 → `deepseek-flash`、窗口 → 1000000 | 口径对齐（幂等可回滚） |
| 10 | `tests/clients/test_deepseek.py` | 增 | UT-45~UT-53 | payload 矩阵与档位/触顶 |
| 11 | `tests/services/test_context_compaction.py` | 增 | UT-54~UT-57 | 缓冲随窗口与触发线回归 |
| 12 | `tests/agents/test_orchestrator.py` | 增 | UT-58~UT-62 + IT-10~IT-12 | reasoning 回流与窗口口径集成 |
| 13 | 配置测试 | 增 | UT-63~UT-65 | 配置边界与枚举校验 |

## 变更清单（实际实施明细）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/core/config.py` | 修 | 增 `llm_max_output_tokens`（默认 32768，`ge=1024, le=393216`）/ `llm_reasoning_effort`（`Literal["low","high","max"]`，默认 high）/ `orchestrator_thinking_enabled`（默认 True）；补 `from typing import Literal` | D2/D3/D4 的配置入口与校验 |
| `app/clients/deepseek.py` | 修 | `MAX_OUTPUT_TOKENS` 注释降级为「兜底默认档」；增 `SUMMARY_MAX_OUTPUT_TOKENS=4096`、`SPEC_BUILDER_MAX_OUTPUT_TOKENS=16384` | 输出档位常量 |
| `app/clients/deepseek.py` | 修 | `_build_payload` 增 `thinking`/`max_tokens`/`temperature`，实现参数矩阵（True → enabled+effort 且**不下发 temperature**；False → disabled 且不下发 effort；None → 旧行为） | D3 参数语义修正 |
| `app/clients/deepseek.py` | 增 | `_check_output_truncated(usage, max_tokens)` | D2 触顶告警 `llm_output_truncated` |
| `app/clients/deepseek.py` | 修 | `chat_completion_stream_raw` 增 `thinking`/`max_tokens`；`chat_completion_messages` 增 `max_tokens`/`thinking`/`temperature`；两条非流式路径均调用触顶检查 | 参数透传与告警覆盖 |
| `app/services/context_compaction_service.py` | 修/增 | 增 `COMPACTION_BUFFER_MAX_TOKENS=100000` / `COMPACTION_BUFFER_RATIO=0.05`（`COMPACTION_BUFFER_TOKENS` 降为下限）；增 `compaction_buffer_tokens(context_window)`；`compaction_threshold` 改用之；`_summarize_with_fallback` 传 `max_tokens=4096` 与 `thinking=settings.orchestrator_thinking_enabled` | D1 缓冲口径 + 摘要档位 |
| `app/agents/orchestrator.py` | 修 | `_build_messages` 增 `with_tools`；assistant 带 `tool_calls` 且 `with_tools` 为真时回传 `reasoning_content = thinking` | D4 路线 B 协议要求 |
| `app/agents/orchestrator.py` | 修 | `chat_stream` 增 `thinking_enabled`；流式循环累积 `reasoning_parts` 写入 `assistant_entry["thinking"]`；四处流式调用统一传 `thinking`；`_stream_plain_answer` / `_stream_fake_tool_correction` 增 `thinking` 参数 | D3 显式化 + D4 持久化 |
| `app/models/llm_provider.py` / `app/schemas/llm_provider.py` / `app/services/llm_provider_service.py` | 修 | `context_window` 默认 65536 → 128000 | 通用兜底默认（旧值明显小于模型能力） |
| `alembic/versions/20260927_llm_params_window.py` | 增 | 迁移 `20260927_llm_params_window`（down_revision=`20260927_task_progress_detail`） | **幂等数据修正**：退役模型名 → `deepseek-flash`；旧默认窗口 65536 → 1000000（仅改仍是旧值的行）；downgrade 仅回滚窗口 |
| `opc-central-dispatch-hub/.env.local` / `.env.example` | 修 | `DEEPSEEK_MODEL` → `deepseek-flash`；增 `LLM_MAX_OUTPUT_TOKENS` / `LLM_REASONING_EFFORT` / `ORCHESTRATOR_THINKING_ENABLED` | 运行与部署模板 |
| `tests/clients/test_deepseek.py` | 修/增 | `_payload_of` / `_call_messages` 辅助；UT-45~UT-53 | 参数矩阵、档位、触顶告警 |
| `tests/services/test_context_compaction.py` | 增 | UT-54~UT-57 + 导入 `compaction_buffer_tokens` | 缓冲随窗口、1M 口径守卫 |
| `tests/agents/test_orchestrator.py` | 修/增 | 6 处流式替身签名补 `thinking`/`max_tokens`；新增 `_ToolThenReasoningClient` + UT-58~UT-61 + IT-10 | reasoning 回流、旧会话回归、窗口端到端 |

## 登记记录

- 2026-09-27：创建骨架（ADR-056 四件套建立；Spec/Harness 同步完成，未写生产代码）。
- 2026-09-27：D4 决策经用户确认（**路线 B**：保留思考 + 回传 `reasoning_content`），
  已回填 ADR-056；路线 A 作为未采纳备选保留备查。
- 2026-09-27：范围界定——工具结果超长裁剪（排查记录 P2 第 8 项）**不在本次范围**
  （独立主题、涉及技能层与产物策略），单独立项。
- 2026-09-27：实施完成并登记上表。实施要点：
  1. **模型名与窗口走数据迁移而非结构迁移**：两者都是「存量数据口径错误」，用幂等
     UPDATE 修正；且只改仍是旧值的行，不覆盖用户手工调整过的窗口（符合 ADR-030 可编辑语义）；
  2. **通用默认窗口取 128000 而非 1000000**：`context_window` 是**多模型通用字段**，
     把通用默认设为某个具体模型的上限会在新增小窗口模型时过度乐观；deepseek 行由迁移精确设 1M；
  3. **摘要调用也统一传 `thinking`**：provider 前缀缓存按消息前缀匹配、与请求参数无关，
     故不影响 ADR-054 的命中率，但参数语义一致更易排查。
- 2026-09-27：**连带修复**——`chat_completion_stream_raw` 新增参数后测试替身未同步签名，
  导致 30 项既有用例 `TypeError: unexpected keyword argument 'thinking'`；
  已同步 6 处替身（`FakeDeepSeekClient` + 5 处内嵌子类）。
- 2026-09-27：测试与端到端结果见下条。
- 2026-09-27：**测试**——`tests/clients/test_deepseek.py` 26 项 + `tests/services/test_context_compaction.py`
  46 项 + `tests/agents/test_orchestrator.py` 46 项，合计 **118 项全过**；改动文件 ruff check/format 全过。
- 2026-09-27：**数据迁移已应用**（`alembic upgrade head`）：`llm_providers` 行由
  `model=deepseek-v4-flash / context_window=65536` 修正为 `model=deepseek-flash / context_window=1000000`；
  `alembic current` = `20260927_llm_params_window (head)`。
- 2026-09-27：**真实后端端到端验证通过**（后端进程启动时间 08:29:26 晚于 `orchestrator.py` 08:21:08，
  确认运行的是含 P2 的新代码；测试会话用后已删除）：
  1. 真实 `chat/stream` 诱导工具调用（"查一下当前有哪些空闲的 code 节点"）；
  2. **无中断**：事件序列含 31 段 `thinking` → `context_usage` → `step_started`/`step_finished`
     （`find_idle_nodes` 成功）→ `chunk` × N → `done`，**无 `error`**；
  3. **窗口口径生效**：`context_usage.window_tokens = 1000000`，真实占用 **7035 token = 0.7%**
     （旧 65536 口径下同一请求会显示 10.7%）；
  4. **思考模式生效**：31 段 `thinking` 增量共 141 字符；落库 `assistant(tool_calls)` 条目
     `thinking` 长度 141（作为后续请求 `reasoning_content` 的来源）；
  5. **新参数生效**：`llm_usage_records` 中 `model=deepseek-flash`，两次调用
     `completion_tokens` 为 58 / 71（远低于新档 32768），**无 `llm_output_truncated` 告警**；
     第二次调用 `prompt_cache_hit_tokens=7040 / 7174`（缓存命中 **98%**，ADR-054 前缀复用继续有效）。
- 2026-09-27：**修复实施中发现的一处遗漏**（同轮续行缺 `reasoning_content`）：
  首版只在 `_build_messages` 里回传 `reasoning_content`，但**同一轮对话内**新增的
  assistant 条目是直接 append 到 `prompt_messages` 的（不重走 `_build_messages`），
  导致本轮后续请求缺该字段。已在构造 `assistant_entry` 时同时写入
  `thinking`（供下一条 HTTP 请求重建历史用）与 `reasoning_content`（供同轮续行直接用）。
  该缺陷由 UT-58 捕获（诊断显示第二条消息 `keys=['content','role','thinking','tool_calls']`
  缺 `reasoning_content`）。
